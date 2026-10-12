// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
/* 基础游戏的 Windows 内容计划。元数据只决定要请求哪些包，许可仍由 Steam 决定。
 * Valve 挂载与共享继承规则：https://partner.steamgames.com/doc/store/application/depots
 * 同名文件由后挂载的 depot 覆盖；数组下标或 depot ID 排序不能冒充原始 mount_order。
 */
export function numericId(value, max = 0xffffffffn, label = 'ID') {
  if (!['string', 'number'].includes(typeof value) || (typeof value === 'number' && !Number.isSafeInteger(value))
      || !/^\d+$/.test(String(value)) || BigInt(value) <= 0n || BigInt(value) > max) {
    const error = new Error(`${label} 必须是范围内的正整数，不接受其他控制台文本。`);
    error.exitCode = 2; throw error;
  }
  return BigInt(value).toString();
}
const tokens = value => value == null || value === '' ? [] : String(value).toLowerCase().split(/[,;\s]+/).filter(Boolean);
const active = value => ![undefined, null, '', false, 0, '0'].includes(value);
function issues(value, label) {
  if (value != null && (!Array.isArray(value) || value.length)) throw new Error(`${label} 的内容包资料不完整：${JSON.stringify(value)}`);
}
function resolveDepot(depot, sharedApps, trail = []) {
  const id = numericId(depot.depot, 0xffffffffn, 'depot');
  issues(depot.metadata_issues, `depot ${id}`);
  if (!active(depot.shared_from_app)) return {...depot, depot:id};
  const source = numericId(depot.shared_from_app, 0xffffffffn, '共享来源 appid');
  const key = `${source}/${id}`;
  if (trail.includes(key) || trail.length >= 32) throw new Error(`共享内容包来源循环或过深：${key}`);
  const app = sharedApps[source];
  if (!app) throw new Error(`共享内容包 ${id} 缺少来源应用 ${source} 的资料`);
  issues(app.depot_metadata_issues, `共享应用 ${source}`);
  const matches = app.depots?.filter(row => String(row.depot) === id) || [];
  if (matches.length !== 1) throw new Error(`共享应用 ${source} 没有唯一对应的 depot ${id}`);
  const master = resolveDepot(matches[0], sharedApps, [...trail, key]);
  // 宿主保留挂载位置，四种选择属性继承 master；仅在宿主缺 public manifest 时补源版本。
  // https://github.com/SteamRE/DepotDownloader/blob/77f9067eaaf1f9ecd2a5427e9059bfe84ee014ad/DepotDownloader/ContentDownloader.cs#L217
  const manifests = depot.manifests?.public ? depot.manifests : master.manifests;
  return {...master, manifests, gid:depot.gid || master.gid, depot:id, mount_order:depot.mount_order, shared_from_app:source,
    shared_chain:[source, ...(master.shared_chain || [])]};
}

export function planDepots(row, options = {}) {
  const appid = numericId(row.appid, 0xffffffffn, 'appid');
  issues(row.depot_metadata_issues, `应用 ${appid}`);
  if (!Array.isArray(row.depots) || !row.depots.length) throw new Error('没有完整的内容包列表，请重新读取游戏资料。');
  const sharedApps = options.sharedApps || row.sharedApps || {};
  const excluded = [], external_dependencies = [], seen = new Set();
  const candidates = [];
  for (const original of row.depots) {
    const id = numericId(original.depot, 0xffffffffn, 'depot');
    if (seen.has(id)) throw new Error(`内容包 ${id} 重复，无法确定完整挂载顺序`);
    seen.add(id);
    // Steam 的公共 Windows 安装器独立于游戏目录；这里记录依赖，绝不把安装器冒充游戏 core。
    // https://partner.steamgames.com/doc/features/common_redist
    if (String(original.shared_from_app) === '228980') {
      issues(original.metadata_issues, `depot ${id}`);
      let source = original, metadata_error = null;
      try { source = resolveDepot(original, sharedApps); } catch (error) { metadata_error = error.message; }
      external_dependencies.push({depot:id, appid:'228980', gid:source.manifests?.public?.gid || source.gid || null,
        kind:'steam_common_redistributable', installed:false, metadata_error});
      continue;
    }
    const depot = resolveDepot(original, sharedApps);
    const config = depot.config ?? {};
    if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error(`depot ${id} 的 config 格式错误`);
    const os = tokens(config.oslist ?? depot.oslist);
    if (os.length && !os.includes('windows')) { excluded.push({depot:id, reason:'other_os'}); continue; }
    // 本命令安装基础游戏。明确的 DLC 条件才可排除；未标 DLC 的必需 core 永不按名字猜测。
    if (active(depot.dlc_appid) || active(config.dlcappid) || active(depot.optional_dlc_appid) || active(config.optionaldlc)) {
      excluded.push({depot:id, reason:'dlc_not_requested', dlc_appid:String(depot.dlc_appid || config.dlcappid || depot.optional_dlc_appid || config.optionaldlc)});
      continue;
    }
    for (const [key, value] of Object.entries(config)) {
      if (!['oslist', 'osarch', 'language', 'dlcappid', 'optionaldlc'].includes(key) && active(value)) {
        throw new Error(`depot ${id} 需要尚未确认的选择条件 ${key}=${value}，未下载部分游戏`);
      }
    }
    if (active(depot.optional) || active(depot.lvcache) || active(depot.systemdefined) || active(depot.shareddepottype)) {
      throw new Error(`depot ${id} 有尚未确认的可选或系统挂载条件`);
    }
    const arch = tokens(config.osarch ?? depot.osarch);
    if (arch.some(value => !['32', '64'].includes(value))) throw new Error(`depot ${id} 的架构条件无法识别`);
    const languages = tokens(config.language ?? depot.language);
    candidates.push({...depot, arch, languages});
  }
  const arches = new Set(candidates.flatMap(depot => depot.arch));
  const architecture = options.architecture || (arches.has('64') ? '64' : arches.has('32') ? '32' : '64');
  if (!['32', '64'].includes(architecture)) throw new Error('Windows 内容架构必须为 32 或 64');
  const compatible = candidates.filter(depot => {
    if (depot.arch.length && !depot.arch.includes(architecture)) { excluded.push({depot:depot.depot, reason:'other_architecture'}); return false; }
    return true;
  });
  const availableLanguages = [...new Set(compatible.flatMap(depot => depot.languages))].sort();
  const requested = options.language ?? row.current_app_language ?? row.steam_language ?? null;
  let language = requested ? String(requested).toLowerCase() : null;
  let language_reason = language ? 'configured' : 'all_languages';
  // 已设置的客户端语言是挂载条件，即便它没有独立语言 depot；常见情况是英文就在 common core。
  // 标签集合描述的是内容包条件，不能用它改写用户选择或推断游戏支持的全部语言。
  if (availableLanguages.length && !language) {
    if (availableLanguages.includes('english')) { language = 'english'; language_reason = 'english_fallback'; }
    else if (availableLanguages.length === 1) { language = availableLanguages[0]; language_reason = 'only_available_language'; }
    else throw new Error(`需要选择游戏语言；可用语言：${availableLanguages.join(', ')}，未下载部分游戏`);
  }
  const selected = [];
  for (const depot of compatible) {
    if (depot.languages.length && !depot.languages.includes(language)) { excluded.push({depot:depot.depot, reason:'other_language'}); continue; }
    const manifest = depot.manifests ? depot.manifests.public : {gid:depot.gid, download:depot.dl, size:depot.size};
    if (!manifest) throw new Error(`depot ${depot.depot} 缺少 public 内容清单，未下载部分游戏`);
    issues(manifest.metadata_issues, `depot ${depot.depot} public manifest`);
    selected.push({depot:depot.depot, gid:numericId(manifest.gid, 0xffffffffffffffffn, 'manifest'),
      download_appid:appid, shared_from_app:depot.shared_from_app ?? null, shared_chain:depot.shared_chain || [],
      mount_order:Number.isSafeInteger(depot.mount_order) && depot.mount_order >= 0 ? depot.mount_order : null,
      dl:manifest.download ?? null, size:manifest.size ?? null,
      reason:depot.languages.length ? 'selected_language' : 'base_content'});
  }
  if (!selected.length) throw new Error('当前条件下没有可下载的基础游戏内容包');
  const orderKnown = selected.every(depot => depot.mount_order !== null)
    && new Set(selected.map(depot => depot.mount_order)).size === selected.length;
  // 老单包接口可继续使用；多包缺少原始顺序时不能把 ID 排序伪装成完整计划。
  if (selected.length > 1 && !orderKnown) throw new Error('多个内容包缺少唯一、完整的 mount_order，未下载部分游戏');
  selected.sort((a,b) => (a.mount_order ?? 0) - (b.mount_order ?? 0));
  return {protocol:1, appid, platform:'windows', branch:'public', scope:'base_game', architecture,
    language, language_reason, requested_language:requested, selected, excluded, external_dependencies,
    mount_order_verified:orderKnown, complete:true};
}
