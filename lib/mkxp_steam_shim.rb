# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# mkxp_steam_shim.rb（dryport，2026-09-14）
# RGSS 游戏的 Steam 成就脚本用 Win32API.new("steam_api", "SteamAPI_ISteamUserStats_GetStat", ...) 找扁平 API；
# Valve 新版 dylib 把几组函数按类型改了名（GetStat → GetStatInt32 / GetStat0 → GetStatFloat 等），
# mkxp-z 的 MiniFFI 找不到符号会直接抛 RuntimeError，脚本没有 rescue，游戏在标题前就死（一款 RPG Maker 游戏 第 364 行）。
# 这里：先按旧名找；找不到按改名表再找一次（参数完全相同，只是名字带了类型）；仍找不到且是 Steam 函数，
# 就给一个「Steam 不在」的桩（call 永远返回 0，即 false / 空），等价于没装 Steam 的状态：游戏照常跑，成就不解锁。
# 不伪造身份、不伪造成就；非 Steam 的库照旧抛错，不掩盖别的问题。

module SnSteamShim
  RENAMES = {
    "SteamAPI_ISteamUserStats_GetStat"  => "SteamAPI_ISteamUserStats_GetStatInt32",
    "SteamAPI_ISteamUserStats_GetStat0" => "SteamAPI_ISteamUserStats_GetStatFloat",
    "SteamAPI_ISteamUserStats_SetStat"  => "SteamAPI_ISteamUserStats_SetStatInt32",
    "SteamAPI_ISteamUserStats_SetStat0" => "SteamAPI_ISteamUserStats_SetStatFloat",
    "SteamAPI_ISteamUserStats_GetUserStat"  => "SteamAPI_ISteamUserStats_GetUserStatInt32",
    "SteamAPI_ISteamUserStats_GetUserStat0" => "SteamAPI_ISteamUserStats_GetUserStatFloat",
    "SteamAPI_ISteamUserStats_GetGlobalStat"  => "SteamAPI_ISteamUserStats_GetGlobalStatInt64",
    "SteamAPI_ISteamUserStats_GetGlobalStat0" => "SteamAPI_ISteamUserStats_GetGlobalStatDouble",
  }

  class Stub
    def initialize(lib, func)
      @lib = lib
      @func = func
      @warned = false
    end

    def call(*_args)
      unless @warned
        @warned = true
        $stderr.puts "[dryport] Steam 函数 #{@func}（#{@lib}）本机不可用，已用桩：返回 0，成就等功能不生效"
      end
      0
    end
    alias_method :Call, :call
  end

  def self.steamish?(lib, func)
    func.to_s.start_with?("SteamAPI_") || lib.to_s =~ /steam_api/i
  end

  # 脚本按 32 位 Windows 写：接口指针声明成 "i"。mkxp-z 的 MiniFFI 在 64 位下把 "i" 参数掩成 32 位
  # （miniffi-binding.cpp：lParam & UINT32_MAX），SteamUserStats() 返回的 64 位指针传回去就被截断，第一次调用即段错误
  # （一款 RPG Maker 游戏：steam achievements:27 Segmentation fault）。Steam 函数一律把 "i" 改成 "l"（不掩位，寄存器里低 32 位不变）。
  def self.widen(types)
    case types
    when String then types.tr("iI", "lL")
    when Array then types.map { |t| t.is_a?(String) ? t.tr("iI", "lL") : t }
    else types
    end
  end
end

if Object.const_defined?(:Win32API)
  class << Win32API
    alias_method :__sn_original_new, :new unless method_defined?(:__sn_original_new)

    def new(lib, func, *rest, &blk)
      if SnSteamShim.steamish?(lib, func)
        rest = rest.dup
        rest[0] = SnSteamShim.widen(rest[0]) if rest.size >= 1
        rest[1] = SnSteamShim.widen(rest[1]) if rest.size >= 2
      end
      __sn_original_new(lib, func, *rest, &blk)
    rescue RuntimeError => error
      alt = SnSteamShim::RENAMES[func.to_s]
      if alt
        begin
          return __sn_original_new(lib, alt, *rest, &blk)
        rescue RuntimeError
        end
      end
      raise error unless SnSteamShim.steamish?(lib, func)
      $stderr.puts "[dryport] #{func}（#{lib}）：#{error.message}；用桩代替"
      SnSteamShim::Stub.new(lib, func)
    end
  end
end
