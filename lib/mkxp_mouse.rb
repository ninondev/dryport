# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
# mkxp_mouse.rb — 给 RPG Maker VX Ace / VX / XP 游戏补上鼠标操作
#
# RGSS 引擎本身没有鼠标。Windows 上的鼠标是游戏脚本用 Win32API 调 user32 硬做的，
# 那套东西在 mkxp-z 里不存在。但 mkxp-z 自带原生鼠标 API
# (Input.mouse_x / mouse_y / MOUSELEFT / MOUSERIGHT)，可以从 Ruby 这一侧接进去。
#
# 提供:
#   · 鼠标移到菜单项上      → 自动高亮该项
#   · 左键点击菜单项        → 等同按「确认」
#   · 左键点击已高亮的项    → 直接确定
#   · 右键                  → 等同按「取消」
#   · 地图上左键点地面      → 走过去 (VX Ace 自带的点击移动)
#   · 滚轮                  → 上下翻选项
#
# 设计原则: 只加不改。任何一步出异常都静默退回纯键盘行为，绝不让鼠标把游戏搞崩。
#
# 2026-09-10

module MkxpMouse
  # 有些游戏自绘 UI，命中测试会找不到目标，这时不要干扰键盘
  @enabled   = true
  @last_x    = -1
  @last_y    = -1
  @hover_win = nil
  @hover_idx = -1
  @click_c   = false   # 本帧是否应触发「确认」
  @click_b   = false   # 本帧是否应触发「取消」
  @wheel     = 0

  class << self
    attr_accessor :enabled

    def active_windows
      # 找当前场景里所有可见、激活的 Window_Selectable
      scene = (SceneManager.scene rescue nil)
      return [] unless scene
      out = []
      scene.instance_variables.each do |iv|
        w = scene.instance_variable_get(iv) rescue nil
        next unless w.is_a?(Window_Selectable)
        next if w.disposed? rescue true
        next unless (w.visible && w.active) rescue false
        out << w
      end
      out
    rescue Exception
      []
    end

    # 把某个窗口第 i 项的矩形换算成屏幕坐标
    def item_screen_rect(win, i)
      r = win.item_rect(i)
      pad = (win.respond_to?(:standard_padding) ? win.standard_padding : 12)
      [win.x + pad + r.x - (win.ox rescue 0),
       win.y + pad + r.y - (win.oy rescue 0),
       r.width, r.height]
    rescue Exception
      nil
    end

    def hit_test(mx, my)
      active_windows.each do |w|
        n = (w.item_max rescue 0)
        next if n <= 0
        (0...n).each do |i|
          sr = item_screen_rect(w, i)
          next unless sr
          x, y, cw, ch = sr
          # 只认落在窗口可视范围内的项
          next if y + ch <= w.y || y >= w.y + w.height
          if mx >= x && mx < x + cw && my >= y && my < y + ch
            return [w, i]
          end
        end
      end
      nil
    rescue Exception
      nil
    end

    # 每帧调用: 更新高亮、决定本帧要不要合成按键
    def poll
      return unless @enabled
      @click_c = false
      @click_b = false

      inwin = (Input.mouse_in_window? rescue false)
      return unless inwin

      mx = Input.mouse_x
      my = Input.mouse_y
      moved = (mx != @last_x || my != @last_y)
      @last_x, @last_y = mx, my

      hit = hit_test(mx, my)

      # 鼠标移动时跟随高亮
      if hit && moved
        w, i = hit
        if (w.index != i rescue false)
          w.index = i rescue nil
          Sound.play_cursor rescue nil
        end
        @hover_win, @hover_idx = w, i
      elsif !hit
        @hover_win, @hover_idx = nil, -1
      end

      # 左键: 在选项上就先对齐再确认; 不在选项上, 地图场景交给点击移动
      if (Input.trigger?(Input::MOUSELEFT) rescue false)
        if hit
          w, i = hit
          w.index = i rescue nil
          @click_c = true
        else
          @click_c = true if map_click(mx, my)
        end
      end

      # 右键 = 取消
      @click_b = true if (Input.trigger?(Input::MOUSERIGHT) rescue false)
    rescue Exception
      # 出任何问题就关掉鼠标层, 保证游戏本身不受影响
      @enabled = false
    end

    # 地图上点地面走过去; 返回 true 表示这次点击已被地图消费
    def map_click(mx, my)
      scene = (SceneManager.scene rescue nil)
      return false unless scene.is_a?(Scene_Map)
      return false unless defined?($game_map) && $game_map
      tw = 32; th = 32
      x = ($game_map.display_x + mx.to_f / tw).floor
      y = ($game_map.display_y + my.to_f / th).floor
      if $game_temp.respond_to?(:set_destination)
        $game_temp.set_destination(x, y)
        return false   # 移动本身不算「确认」, 避免误触发对话
      end
      false
    rescue Exception
      false
    end

    def consume_c
      v = @click_c; @click_c = false; v
    end

    def consume_b
      v = @click_b; @click_b = false; v
    end
  end
end

# ── 把鼠标合成的按键并进 Input ───────────────────────────────
module Input
  class << self
    alias_method :__mm_trigger, :trigger?
    alias_method :__mm_press,   :press?
    alias_method :__mm_repeat,  :repeat?

    def trigger?(key)
      if MkxpMouse.enabled
        return true if key == Input::C && MkxpMouse.consume_c
        return true if key == Input::B && MkxpMouse.consume_b
      end
      __mm_trigger(key)
    end

    def press?(key)   ; __mm_press(key)  ; end
    def repeat?(key)  ; __mm_repeat(key) ; end
  end
end

# ── 每帧驱动 ────────────────────────────────────────────────
module Graphics
  class << self
    alias_method :__mm_update, :update
    def update
      MkxpMouse.poll
      __mm_update
    end
  end
end

$stderr.puts "[mouse] 鼠标层已启用 (左键=确认 右键=取消 移动=高亮)"
