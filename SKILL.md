---
name: Windows 窗口录制管线（本机实测可用）
description: >-
  【Windows 窗口录制管线（本机实测可用）】>- 【Windows 窗口录制管线（本机实测可用）】>- 在 Windows（本机为 4K 250% 缩放）上稳定录制某个窗口/区域为规范 MP4 的完整管线与踩坑。 当需要「录屏/录一段视频/录制游戏画面/把窗口画下来做成 30 秒视频」， 或 ffmpeg 的 gdigrab 报 `Capture area extends outside window area`、 录出来是黑屏/录到了别的窗口/分辨率很低时使用。 覆盖：DPI 感知、mss→ffmpeg 管道、SWP_NOSENDCHANGING 改窗口尺寸、遮挡窗口处理、16:9 裁剪出片。
agent_created: true
---

# Windows 窗口录制管线（本机实测可用）

## 适用与信号
- 用户要「录屏 / 录 30 秒 / 录游戏画面 / 做个宣传视频」
- `ffmpeg -f gdigrab` 报 `Capture area (…) extends outside window area`
- 录出来是黑屏、录到了别的窗口、或分辨率远低于预期

## 核心结论（先看这个）

1. **不要用 ffmpeg 的 gdigrab。** 本机 `D:\Tools\ffmpeg\bin\ffmpeg.exe`（ffmpeg 21.7 custom build，RTX 5060 Ti 可用 NVENC）
   是**非 DPI 感知**进程，它看到的"桌面"只有 `1645×925`（真实 3840×2160，缩放系数 ≈2.334）。
   你对它说 1920×1080 的区域，它会判定越界并直接失败。
   → 改用 **mss 抓帧 → 管道喂 ffmpeg**。

2. **Python 端必须先声明 DPI 感知**，否则窗口坐标是虚拟坐标、和 mss 的物理像素错配（抓错区域、分辨率暴跌）：
   ```python
   import ctypes
   try: ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE_V2
   except Exception: ctypes.windll.user32.SetProcessDPIAware()
   ```
   声明后 `GetClientRect` / `ClientToScreen` 返回的就是物理像素，可直接交给 mss。

3. **改别的程序的窗口尺寸**（游戏、全屏应用）：普通 `SetWindowPos` 会被对方在 `WM_WINDOWPOSCHANGING` 里否决
   （表现为：你请求 3860×2130，结果窗口变成 1110×630）。
   必须加 **`SWP_NOSENDCHANGING (0x0400)`**：
   ```python
   style = u.GetWindowLongW(hwnd, -16)          # GWL_STYLE
   r = RECT(0,0,1920,1080); u.AdjustWindowRect(ctypes.byref(r), style, False)
   u.SetWindowPos(hwnd, 0, 0, 0, r.right-r.left, r.bottom-r.top,
                  0x0004 | 0x0020 | 0x0400)   # NOZORDER|FRAMECHANGED|NOSENDCHANGING
   ```
   实测：请求客户区 1920×1080 → 窗口 1932×1114 → 客户区 1920×1079。

4. **录屏前必须处理遮挡窗口——但绝对不许「压制」用户自己的 WorkBuddy。**
   mss/ddagrab 抓的都是**屏幕合成结果**，被盖住就录到别人。但 2026-09-19 实测踩过大坑：
   写了个「全程隐藏 WorkBuddy」的看门狗，结果用户想打开 WorkBuddy 跟助手说话时被反复按下去，
   用户以为是自己电脑坏了，最后**重启了电脑**。
   → 规则：录制脚本里**只允许**「点一次标题栏把目标窗口拿到前台」，
   **禁止**任何 `ShowWindow(WB, 0)` / 循环隐藏 / 给目标窗口设 TOPMOST 的操作。
   正确做法是：让用户自己把 WorkBuddy 挪开或最小化（录前一句话交代清楚），
   录完立刻把焦点/置顶状态还给用户。
   历史做法（已废弃，仅作参考）：
   ```bash
   mc.py win-list / win-hide --hwnd <h> / win-show --hwnd <h>
   ```

## 标准流程

```bash
# 1) 找一个 python（要有 mss / Pillow；本机用 WorkBuddy managed venv）
PYV="C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

# 2) 跑录制脚本（脚本见 scripts/capture.py）
#    capture.py <hwnd> <dur_sec> [resize_w resize_h] [pan_px]
"$PYV" -u capture.py 1234567 30 1920 1080 0
```

脚本做什么：
1. `focus()`：ALT 键技巧 + BringWindowToTop + SetForegroundWindow 抢前台
2. `resize_client()`：把客户区调成 1920×1080（见上面 SWP_NOSENDCHANGING）
3. `GetClientRect` + `ClientToScreen` 拿物理坐标（此时已 DPI 感知）
4. `ffmpeg -f rawvideo -pix_fmt bgra -s WxH -r 30 -i pipe:0 -vf "crop=…,scale=1920:1080,setsar=1"`
   `-c:v h264_nvenc -preset p4 -b:v 9M -pix_fmt yuv420p -t <dur> out.mp4`
5. 主循环：`mss.grab(mon)` → 写 stdin；可选按 `PAN*elapsed/dur` 均匀发相对鼠标移动做平移
6. 打印 `frames / secs / bytes` 自检

**帧率自检**：`frames / secs` 应 ≈30。若只有 ~15fps，说明抓的区域太大（4K 抓帧 ≈29MB/帧），
把客户区缩到 1920×1080 即可回到 30fps。

## 出片（16:9 + 音轨）

平台通常要求 H.264 / 16:9 / ≤50MB / ≤90s。
```bash
ffmpeg -y -i raw.mp4 -f lavfi -i anullsrc=r=44100:cl=stereo \
  -vf "crop=1580:888:340:100,scale=1920:1080:flags=lanczos,setsar=1" \
  -c:v h264_nvenc -preset p4 -b:v 9M -pix_fmt yuv420p \
  -c:a aac -b:a 128k -shortest -movflags +faststart out.mp4
```
- 用 `setsar=1` 保证 DAR 正好 16:9。
- 裁剪去掉游戏 UI/调试浮层：把左上角的坐标文字、hotbar、客户端悬浮按钮都排除掉；
  注意**右侧的 UI 按钮如果只裁掉一半会更难看**，要么全留、要么整块切掉。
- 封面图：对同一时间点抽帧，套同样的 crop 再 `scale=992:558`（尺寸按平台要求）。

## 坑清单
- `dump_dialog.py` 一类靠窗口标题匹配文件框的脚本可能误报 `NO_DIALOG`，但对话框其实已打开 →
  先截图确认，别急着重试。
- 文件框切到目标目录后，**只能输入文件名**；输全路径时开头的盘符（`D:`）会被吞掉。
- `mc.py key` 是瞬时按键，**游戏内无效**；游戏要 keyDown→sleep→keyUp 的长按式输入。
- Windows 文件框输入中文/长路径用 `utype.py`（SendInput + KEYEVENTF_UNICODE）。

---

## 【2026-09-19 新方案】4K 屏首选 ffmpeg ddagrab（比 mss 管道强得多）

`mss` 管道在 4K 上**只能跑 ~21fps**，但 ffmpeg 被要求按 30fps 收帧 →
视频实际被**快进 1.4 倍**（70s 的动作压成 49.8s），音画全不同步。
用 ffmpeg 的 Desktop Duplication 抓屏可稳定 30fps **实时**（GPU 拷贝，几乎不吃 CPU）：

```bash
# ✅ 可用（实测 2s 正好 60 帧，接近 30fps 实时）
ffmpeg -hide_banner -y -init_hw_device d3d11va=d3d \
  -filter_complex "ddagrab=output_idx=0:framerate=30:video_size=WxH:offset_x=X:offset_y=Y,\
hwdownload,format=bgra,scale=1920:1080:flags=lanczos" \
  -c:v h264_nvenc -preset p4 -b:v 14M -t 60 out.mp4
```

### 三个必踩的坑（都已实测确认）

1. **`-init_hw_device` 必须带设备名**：`d3d11va=d3d`。
   只写 `d3d11va` 会「成功」但输出 0 字节、`Output file is empty`。
2. **链条里 `crop` 和 `scale=…:flags=lanczos` 不能同时出现** →
   NVENC 报 `CreateInputBuffer failed: invalid param (8): EncodeAPI Internal Error`，输出 0 字节。
   → 用 ddagrab **自带的** `video_size=WxH:offset_x:X:offset_y:Y` 做区域裁剪，后面只留 `scale`。
3. **绝对不要加 `-pix_fmt yuv420p`**！
   在 `hwdownload` 之后的链条上强制 pix_fmt 会让 NVENC 初始化失败（同样报 param (8)）。
   去掉就正常。输出仍是 yuv420p（自动协商）。

### 音频：pyaudiowpatch WASAPI 回环，别和抓帧抢主线程

`mss` 方案里音频线程会和 4K 抓帧抢 GIL → 实测 70s 只录到 61s 样本（丢 13%）。
改用 ddagrab 后 **ffmpeg 在独立进程里抓视频**，Python 主线程只录音频即可：
```python
dev = p.get_default_wasapi_loopback()
CHUNK = 4096                      # 别用 2048
while time.time() - t0 < dur:
    frames.append(stream.read(CHUNK, exception_on_overflow=False))
```
实测 `wall=7.0s / samples=7.0s`，零丢样。

### 音视频不同步的抢救（已经录废了也能救）

若视频是「30fps 声明、实际 N fps」拍出来的，用 `setpts` 拉回真实时长：
```bash
# 真实 70.1s / 视频 49.83s → 系数 1.40678
ffmpeg -y -ss 1.0 -t 7.5 -i raw.mp4 -vf "setpts=PTS*1.40678,fps=30" ... seg_v.mp4
# 音频 61.14s 样本 / 70.1s 墙钟 → atempo 0.87218 拉长回去
ffmpeg -y -ss 1.227 -t 9.203 -i audio.wav -af "atempo=0.87218" -c:a aac seg_a.m4a
```

## 擦掉游戏自带 HUD / 调试文字：用 `removelogo`

本机 ffmpeg **没有** `delogo`、`eq`、`boxblur`，但**有** `removelogo`、`drawbox`、
`lutrgb`、`curves`、`colorlevels`（提亮用 `lutrgb=r='clip(val*1.18,0,255)':...`）。

```python
# 1) 生成同尺寸黑白蒙版：白色=要擦掉的区域
m = Image.new('L', (1920, 1080), 0); ImageDraw.Draw(m).rectangle([14,158,1560,300], fill=255)
m.save('mask.png')
```
```bash
# 2) 蒙版路径**不能用带盘符的绝对路径**（冒号会被当成选项分隔符）→ cd 到目录后用相对名
cd <dir> && ffmpeg -y -ss 8 -i in.mp4 -frames:v 1 -vf "removelogo=mask.png,scale=992:558,setsar=1" cover.jpg
```
- **蒙版四边必须内缩 ~14px**，贴着画面边缘会让补丁边缘冒出**洋红三角**。
- 先用 Python 量出文字真实行范围再画蒙版，别目测：
  `mask=(im[:,:,0]>170)&(im[:,:,1]>170)&(im[:,:,2]>170); rows=mask.sum(axis=1)` —— 实测文字在 y≈175~275，物品栏在 y≈1000~1080。
- 想「裁掉」文字往往不可行：**文字正好压在角色头部那条带上**，裁了就把头切了，只能擦不能裁。

## 游戏侧注意（Bedrock / Minecraft.Windows.exe）
- 窗口类名 `OGLES`，进程 `Minecraft.Windows.exe`；**窗口句柄会变**（重启/重载）→ 脚本每次现找，别写死 hwnd。
- 最大化状态下 `SetWindowPos` 改不动尺寸（客户区会一直是 3840×1984）→ 想缩窗口要先 `ShowWindow(h, SW_RESTORE)`。
- 左上三行调试浮层（`v1.21.120 … FPS … Local=`）是 **F3 调试界面**：
  **F3 是「下一页」不是开关**，页表里含 `Off (Build info)` / `Nothing (No build info)`；F4 上一页。
- 拿前台最可靠的办法是**真实鼠标点标题栏**：`SetCursorPos(中心, top+12)` + mouse_event down/up。
  `SetForegroundWindow` 常被系统前台锁拒掉（返回 False），且**对最小化窗口必然失败**（先 restore）。
