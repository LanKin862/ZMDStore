import pyautogui
import pygetwindow as gw
import time
import sys
import argparse
import cv2
import mss
import numpy as np
from collections import namedtuple
import os
import threading


# --- 性能优化设置 ---
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.1
# scale: 命中时的金字塔层缩放比例, 供定点复查复用; 旧调用不受影响(默认 None)
Point = namedtuple('Point', ['x', 'y', 'scale'], defaults=[None]) # 模拟 pyautogui 的坐标对象

# 全局字典，保存每个图片的最佳置信度
confidence_cache = {}
# 图片对象缓存，避免重复读取硬盘
image_obj_cache = {}

class FastMatcher:
    """使用 mss + OpenCV 的高速匹配类"""
    # 屏幕金字塔尺度（屏幕下采样比例）。物品在游戏 UI 中的实际渲染尺寸
    # 可能与模板不一致（实验测得分离芯为模板的 1.2 倍），逐层匹配覆盖
    # 0.71~1.0 倍屏幕尺度，即物品尺寸 1.0~1.4 倍模板的场景
    PYRAMID_SCALES = [1.0, 0.9, 0.833, 0.77, 0.714]
    # 层内融合得分达到该值即提前返回，普通物品在 1.0 层命中时零额外开销
    EARLY_EXIT_SCORE = 0.90
    # 两阶段匹配参数：
    #   阶段1 无 mask 候选下限（宽松，宁多收）
    CANDIDATE_FLOOR = 0.50
    #   阶段2 带 mask 验证下限（严格，等价原 MIN_CANDIDATE_VAL）
    VERIFY_GATE = 0.75
    #   阶段1 每层候选数
    TOP_N = 2

    def __init__(self, resolution="2560x1440"):
        self.sct = mss.mss()

        target_monitor = self.sct.monitors[1]
        if resolution == "自动检测" or resolution == "Auto":
            # 尝试通过窗口标题查找游戏窗口
            titles = ["endfield", "明日方舟：终末地", "Arknights: Endfield"]
            target_win = None
            for t in titles:
                wins = gw.getWindowsWithTitle(t)
                if wins:
                    target_win = wins[0]
                    break

            if target_win:
                # 寻找窗口中心点所在的显示器
                cx, cy = target_win.left + target_win.width // 2, target_win.top + target_win.height // 2
                for i, m in enumerate(self.sct.monitors):
                    if i == 0: continue
                    if (m["left"] <= cx < m["left"] + m["width"] and
                        m["top"] <= cy < m["top"] + m["height"]):
                        target_monitor = m
                        break
                print(f"自动检测: 发现游戏窗口 '{target_win.title}'")
            else:
                print("自动检测: 未发现游戏窗口，将使用主显示器")

            resolution = f"{target_monitor['width']}x{target_monitor['height']}"
            print(f"自动选择屏幕分辨率: {resolution}")

        full_w = target_monitor["width"]
        full_h = target_monitor["height"]
        self.full_w = full_w
        self.mon_left = target_monitor["left"]
        self.mon_top = target_monitor["top"]

        # 解析用户选择的分辨率，计算缩放因子
        res_parts = resolution.split('x')
        self.target_w = int(res_parts[0]) if len(res_parts) == 2 else 2560
        self.target_h = int(res_parts[1]) if len(res_parts) == 2 else 1440
        self.scale_factor = self.target_w / 2560.0

        # 1. 基础尺寸（原始的 76%x58%）
        self.rw = int(full_w * 0.76)
        base_rh = int(full_h * 0.58)

        # 2. 原始居中时的偏移量 (增加显示器全局偏移)
        self.rx = int((full_w - self.rw) / 2) + self.mon_left
        base_ry = int((full_h - base_rh) / 2) + self.mon_top

        # 3. 上边增高 100 像素
        # 顶部偏移量减少 100 (向上延伸)
        self.ry = max(self.mon_top, base_ry - 100)
        # 总高度增加 200
        self.rh = base_rh + 200

        # 构造搜索区域
        self.search_region = {
            "top": self.ry,
            "left": self.rx,
            "width": self.rw,
            "height": self.rh
        }
        print(f"区域调整：当前区域 {self.rw}x{self.rh}，偏移({self.rx}, {self.ry})")

    def load_image(self, path):
        """预读图片 + 自动裁掉透明背景"""
        if path in image_obj_cache:
            return image_obj_cache[path]

        # 使用 np.fromfile + imdecode 以支持中文/Unicode 路径
        try:
            img_data = np.fromfile(path, dtype=np.uint8)
            img = cv2.imdecode(img_data, cv2.IMREAD_UNCHANGED)
        except Exception as e:
            print(f"错误: 无法加载图片 {path}, 异常: {e}")
            return None

        if img is None:
            print(f"错误: 无法加载图片 {path}")
            return None

        is_item = "item" in path.replace("\\", "/").split("/")

        # 缩放前：仅填充完全透明的像素（alpha==0），用不透明区域的 BGR 均值。
        # 半透明边缘像素（alpha 1~254）保留原始颜色——它们参与颜色直方图计算，
        # 混入灰色会降低 color 得分。
        # 同时对 alpha<=5 的像素归零，消灭 canvas 预乘 alpha 往返产生的量化噪点。
        if len(img.shape) == 3 and img.shape[2] == 4:
            alpha = img[:, :, 3]
            # 消灭 canvas 量化噪点：极低 alpha 一律归零
            noise_mask = alpha <= 5
            img[noise_mask, 3] = 0
            alpha = img[:, :, 3]  # 刷新
            # 纯透明像素用主体均值填充（仅为了 resize 插值干净）
            solid_mask = alpha == 255
            if np.any(solid_mask):
                mean_color = img[solid_mask][:, :3].mean(axis=0)
            else:
                mean_color = np.array([50.0, 50.0, 50.0])
            transparent_mask = alpha == 0
            img[transparent_mask, :3] = mean_color.astype(np.uint8)

        # 强制检查并缩放 item 图片：基准为 2560x1440 屏幕下对应 128x128 的模版
        if is_item:
            target_w = getattr(self, "target_w", 2560)
            target_h = getattr(self, "target_h", 1440)
            target_item_w = int(128 * (target_w / 2560.0))
            target_item_h = int(128 * (target_h / 1440.0))

            if img.shape[1] != target_item_w or img.shape[0] != target_item_h:
                img = cv2.resize(img, (target_item_w, target_item_h), interpolation=cv2.INTER_AREA)
        else:
            # 2. 非 item 图片 (public, region)：按当前屏幕相对于 2560x1440 的比例进行等比缩放
            if hasattr(self, "scale_factor") and self.scale_factor != 1.0:
                new_w = int(img.shape[1] * self.scale_factor)
                new_h = int(img.shape[0] * self.scale_factor)
                if new_w > 0 and new_h > 0:
                    img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

        # === 临时调试: 输出图片到 temp 文件夹 ===

        try:
            temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
            os.makedirs(temp_dir, exist_ok=True)
            base_name = os.path.basename(path)
            name, ext = os.path.splitext(base_name)
            res_key = f"{getattr(self, 'target_w', 2560)}x{getattr(self, 'target_h', 1440)}"
            debug_path = os.path.join(temp_dir, f"scaled_{res_key}_{name}.png")
            # 使用 imencode 支持含中文或特殊字符路径
            _, encoded_img = cv2.imencode(".png", img)
            encoded_img.tofile(debug_path)
            print(f"[Debug] 已将加载的 item 图片保存至: {debug_path}")
        except Exception as e:
            print(f"[Debug] 调试图片保存失败 {path}: {e}")
        # === 调试结束 ===

        # 获取图片高度和宽度
        h, w = img.shape[:2]
        print(f"图片高度: {h}, 图片宽度: {w}")
        # 如果是物品图片 (item 文件夹)，则对底部进行遮罩
        # 施加一个遮罩，自动裁剪从底部开始，高度占全图 25% 的内容，使其透明化
        if is_item:
            # 如果是 3 通道的图，先转为 4 通道（带 alpha）
            if img.shape[2] == 3:
                alpha_channel = np.ones((h, w), dtype=np.uint8) * 255
                img = cv2.merge((img, alpha_channel))

            # 将底部 25% 的区域 alpha 设为 0
            mask_h = int(h * 0.75)
            img[mask_h:, :, 3] = 0

        # 仅对 item 图片进行蒙版处理提取及根据 alpha 通道裁剪
        if is_item and len(img.shape) == 3 and img.shape[2] == 4:
            bgr = img[:, :, :3]
            alpha = img[:, :, 3]

            # 找到非透明区域（alpha > 0）
            coords = np.column_stack(np.where(alpha > 0))
            if coords.size > 0:
                y_min, x_min = coords.min(axis=0)
                y_max, x_max = coords.max(axis=0)

                # 按照非透明区域进行最终裁剪
                bgr = bgr[y_min:y_max+1, x_min:x_max+1]
                mask = alpha[y_min:y_max+1, x_min:x_max+1]

            else:
                # 全透明，直接返回 None（避免后续匹配异常）
                print(f"警告: 图片全透明 {path}")
                return None

            image_obj_cache[path] = (bgr, mask)
            return bgr, mask

        else:
            # 如果不是 item，或者没有 alpha 通道，直接用
            # 注意：matchTemplate 需要模板和底图通道数一致 (BGR 3通道)
            if len(img.shape) == 3 and img.shape[2] == 4:
                img = img[:, :, :3]

            image_obj_cache[path] = (img, None)
            return img, None

    def grab_screen_img(self):
        screenshot = np.array(self.sct.grab(self.search_region))
        return cv2.cvtColor(screenshot, cv2.COLOR_BGRA2BGR)

    def wait_for_idle(self, timeout=10, check_interval=0.15, tolerance=0.05):
        """
        等待画面静止（动画结束），增加容错率以应对动态 UI（如光标闪烁）
        tolerance: 允许变化的面积占比，默认为 5%
        """
        start_time = time.time()
        last_img = self.grab_screen_img()
        total_pixels = last_img.shape[0] * last_img.shape[1]

        while time.time() - start_time < timeout:
            ensure_not_stopped()
            time.sleep(check_interval)
            curr_img = self.grab_screen_img()

            # 计算两帧之间的差异
            diff = cv2.absdiff(last_img, curr_img)
            # 将彩色差异转为灰度并二值化（忽略亮度变化小于 25 的微小干扰）
            gray_diff = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray_diff, 25, 255, cv2.THRESH_BINARY)

            # 计算发生变化的像素点数
            changed_pixels = np.count_nonzero(thresh)
            change_ratio = changed_pixels / total_pixels

            # 如果变化面积小于总面积的 tolerance%
            if change_ratio < tolerance:
                print(f"画面静止，继续执行后续操作, 耗时: {time.time() - start_time}")
                return True

            last_img = curr_img
        return False

    def _collect_candidates(self, screen_img, core_target, core_w, core_h, search_region):
        """阶段1: 无 mask 全屏金字塔匹配，收集各层 top-N 候选（层坐标）。

        mask 匹配每层约 600ms，无 mask 快一个数量级。先快速定位候选，
        精确验证留给阶段2 的局部 ROI，从而避免全屏 mask 匹配的巨额开销。
        返回 (去重后的候选列表, 层屏幕缓存)。
        """
        candidates = []
        levels = {}
        for scale in self.PYRAMID_SCALES:
            if abs(scale - 1.0) < 1e-6:
                level = screen_img
            else:
                sw = max(16, int(screen_img.shape[1] * scale))
                sh = max(16, int(screen_img.shape[0] * scale))
                level = cv2.resize(screen_img, (sw, sh), interpolation=cv2.INTER_AREA)
            levels[scale] = level

            res = cv2.matchTemplate(level, core_target, cv2.TM_CCOEFF_NORMED)
            res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
            rcopy = res.copy()

            # 左右区域限制（层坐标，限制线按层缩放）
            if search_region in ("left", "right"):
                limit_x = int((self.full_w * 0.58 - (self.rx - self.mon_left)) * scale - core_w // 2)
                if 0 < limit_x < rcopy.shape[1]:
                    if search_region == "left":
                        rcopy[:, limit_x:] = -1
                    else:
                        rcopy[:, :limit_x] = -1

            # 每层取 top-N 候选（NMS 压制）
            for _ in range(self.TOP_N):
                _, mv, _, ml = cv2.minMaxLoc(rcopy)
                if mv < self.CANDIDATE_FLOOR:
                    break
                candidates.append((scale, ml[0], ml[1], mv))
                y1 = max(0, ml[1] - core_h // 2)
                y2 = min(rcopy.shape[0], ml[1] + core_h // 2 + 1)
                x1 = max(0, ml[0] - core_w // 2)
                x2 = min(rcopy.shape[1], ml[0] + core_w // 2 + 1)
                rcopy[y1:y2, x1:x2] = -1

        # 按原屏坐标 8px 网格去重（同一物品会跨层重复出现），保留得分最高者
        buckets = {}
        for scale, lx, ly, mv in candidates:
            ox, oy = int(lx / scale), int(ly / scale)
            key = (round(ox / 8.0), round(oy / 8.0))
            if key not in buckets or mv > buckets[key][3]:
                buckets[key] = (scale, lx, ly, mv)
        return list(buckets.values()), levels

    def _verify_candidate(self, level, scale, lx, ly, core_target, core_mask,
                          core_x, core_y, core_w, core_h, w, h, h1):
        """阶段2: 在候选所属尺度层的局部 ROI 上做带 mask 精确匹配 + 亮度门 + 颜色验证。

        返回 (绝对坐标 Point, 融合得分) 或 (None, 0.0)。
        """
        BRIGHTNESS_GATE = 60
        pad = 8
        x1 = max(0, lx - pad)
        y1 = max(0, ly - pad)
        x2 = min(level.shape[1], lx + core_w + pad)
        y2 = min(level.shape[0], ly + core_h + pad)
        if x2 - x1 < core_w or y2 - y1 < core_h:
            return None, 0.0
        roi = level[y1:y2, x1:x2]

        _use_mask = core_mask is not None and not np.all(core_mask)
        res = cv2.matchTemplate(roi, core_target, cv2.TM_CCOEFF_NORMED,
                                mask=core_mask if _use_mask else None)
        if _use_mask:
            res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val < self.VERIFY_GATE:
            print(f"  [验证 scale={scale:.3f} pos=({x1 + max_loc[0]},{y1 + max_loc[1]})] score={max_val:.3f} < {self.VERIFY_GATE}，拒绝")
            return None, 0.0

        core_roi = roi[max_loc[1]:max_loc[1] + core_h, max_loc[0]:max_loc[0] + core_w]
        if core_roi.shape[:2] != (core_h, core_w):
            return None, 0.0

        hsv_core_target = cv2.cvtColor(core_target, cv2.COLOR_BGR2HSV)
        hsv_core_roi = cv2.cvtColor(core_roi, cv2.COLOR_BGR2HSV)

        # 亮度过滤门
        if core_mask is not None:
            mean_target = cv2.mean(hsv_core_target[:, :, 2], mask=core_mask)[0]
            mean_roi    = cv2.mean(hsv_core_roi[:, :, 2], mask=core_mask)[0]
        else:
            mean_target = np.mean(hsv_core_target[:, :, 2])
            mean_roi    = np.mean(hsv_core_roi[:, :, 2])

        v_diff = abs(mean_target - mean_roi)
        if v_diff > BRIGHTNESS_GATE:
            print(f"  [验证 scale={scale:.3f} pos=({x1 + max_loc[0]},{y1 + max_loc[1]})] score={max_val:.3f} V差={v_diff:.1f} 超出亮度门")
            return None, 0.0

        # 颜色直方图相关性（h1 模板侧已预计算）
        h2 = cv2.calcHist([hsv_core_roi], [0, 1], core_mask, [30, 32], [0, 180, 0, 256])
        cv2.normalize(h2, h2)
        c_score = cv2.compareHist(h1, h2, cv2.HISTCMP_CORREL)
        final_score = 0.91 * max_val + 0.09 * max(0.0, c_score)

        # 精确核心左上角（层坐标）→ 完整模板中心 → 原屏绝对坐标
        exact_lx = x1 + max_loc[0]
        exact_ly = y1 + max_loc[1]
        abs_x = int((exact_lx - core_x + w // 2) / scale) + self.rx
        abs_y = int((exact_ly - core_y + h // 2) / scale) + self.ry
        print(f"  [验证 scale={scale:.3f} pos=({exact_lx},{exact_ly})] core_score={max_val:.3f} V差={v_diff:.1f} color={c_score:.3f} final={final_score:.3f} ✓")
        return Point(abs_x, abs_y, scale), float(final_score)

    def locate_on_screen(self, screen_img, image_path, search_region="all"):
        target_data = self.load_image(image_path)
        if target_data is None:
            return None, 0.0
        target, mask = target_data

        is_item = "item" in image_path.replace("\\", "/").split("/")

        h, w = target.shape[:2]

        # 核心匹配区域
        use_core = is_item and not getattr(self, "liquid_mode", False)
        if use_core:
            # 针对 item：提取中心 50% 宽高的区域（面积 1/4）
            core_w = int(w * 0.50)
            core_h = int(h * 0.50)
            core_x = int((w - core_w) / 2)
            core_y = int((h - core_h) * 0.8)
        else:
            # 针对非 item (如 region, public 等)：使用完整图像
            core_x = 0
            core_y = 0
            core_w = w
            core_h = h

        core_target = target[core_y:core_y+core_h, core_x:core_x+core_w]
        core_mask = mask[core_y:core_y+core_h, core_x:core_x+core_w] if mask is not None else None

        # === 临时调试: 输出核心区域图片到 temp 文件夹 ===
        # 带 alpha 通道写入，透明区域在 PNG 中真正透明，而非显示灰色填充
        if is_item:
            try:
                temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
                os.makedirs(temp_dir, exist_ok=True)
                base_name = os.path.basename(image_path)
                name, ext = os.path.splitext(base_name)
                debug_path = os.path.join(temp_dir, f"core_{name}.png")
                debug_img = (np.dstack((core_target, core_mask)) if core_mask is not None
                             else core_target)
                _, encoded_img = cv2.imencode(".png", debug_img)
                encoded_img.tofile(debug_path)
            except Exception as e:
                print(f"[Debug] 调试核心图片保存失败 {image_path}: {e}")
        # === 调试结束 ===

        # 模板侧颜色数据预计算（验证阶段复用）
        hsv_core_target = cv2.cvtColor(core_target, cv2.COLOR_BGR2HSV)
        h1 = cv2.calcHist([hsv_core_target], [0, 1], core_mask, [30, 32], [0, 180, 0, 256])
        cv2.normalize(h1, h1)

        # 两阶段匹配：
        #   阶段1 无 mask 全屏金字塔找候选（快），阶段2 对候选局部 ROI 做
        #   带 mask 精确验证（准）。既解决物品渲染尺寸与模板不一致的问题
        #   （金字塔覆盖 1.0~1.4 倍），又避免全屏 mask 匹配的巨额开销。
        candidates, levels = self._collect_candidates(screen_img, core_target, core_w, core_h, search_region)
        print(f"  候选数: {len(candidates)}")

        best_pos, best_score = None, 0.0
        for scale, lx, ly, _ in candidates:
            pos, score = self._verify_candidate(levels[scale], scale, lx, ly, core_target, core_mask,
                                                core_x, core_y, core_w, core_h, w, h, h1)
            if pos is not None:
                if score >= self.EARLY_EXIT_SCORE:
                    return pos, score
                if score > best_score:
                    best_pos, best_score = pos, score

        return best_pos, best_score


# 初始化匹配器 (延迟到任务开始时在主逻辑中初始化)
matcher: FastMatcher | None = None
stop_event = threading.Event()

def get_args_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("-t", "--times", type=int, default=30, help='执行次数')
    parser.add_argument("-e", "--end", type=str, default='./region/wuling.png', help='终点')
    parser.add_argument("-b", "--begin", type=str, default='./region/forth_valley.png', help='起点')
    parser.add_argument("-i", "--item", help='物品')
    parser.add_argument("-r", "--resolution", type=str, default='2560x1440', help='屏幕分辨率')
    return parser


def request_stop():
    stop_event.set()


def reset_stop():
    stop_event.clear()


def should_stop():
    return stop_event.is_set()


def ensure_not_stopped():
    if should_stop():
        raise KeyboardInterrupt("Transport stopped by user")

call_id = 0

def _locate_image(image_path, confidence=0.86, timeout=10, min_conf=0.85, step=0.01, search_region=None):
    global call_id
    if matcher is None:
        print("错误: matcher 未初始化")
        return None, 0.0

    call_id += 1
    cid = call_id

    is_item = "item" in image_path.replace("\\", "/").split("/")
    if search_region is None:
        search_region = "left" if is_item else "all"

    current_confidence = confidence_cache.get(image_path, confidence)
    screen_img = matcher.grab_screen_img()
    pos, max_val = matcher.locate_on_screen(screen_img, image_path, search_region=search_region)

    # 打印日志
    val_display = f"{max_val:.3f}" if max_val is not None else "0.000"
    print(f"[{cid}] {image_path} score={val_display} init_thr={current_confidence:.2f} min_thr={min_conf:.2f} pos={'Y' if pos else 'N'}")

    if not pos or max_val is None:
        print(f"[{cid}] RETURN None (no pos)")
        return None, float(max_val or 0.0)

    if max_val >= float(current_confidence):
        confidence_cache[image_path] = current_confidence
        print(f"[{cid}] RETURN pos (score>=init_thr)")
        return pos, float(max_val)

    conf = float(current_confidence)
    while conf > float(min_conf):
        conf = round(conf - step, 2)
        print(f"[{cid}] TRY thr={conf:.2f} score={val_display}")
        if max_val >= conf:
            confidence_cache[image_path] = conf
            print(f"[{cid}] RETURN pos (score>=thr)")
            return pos, float(max_val)

    print(f"[{cid}] RETURN None (score<{min_conf:.2f})")
    return None, float(max_val)

# === 位置缓存表: (image_path, 区域语义) -> Point(x, y, scale) ===
# 首次实时匹配成功后记录; 后续同图同区域直接定点匹配(局部 ROI), 失败自动回退实时并刷新。
# 位置固定元素(UI按钮/地图标记/仓库格子)由此免去每轮全屏金字塔查找。
pos_cache = {}

def _cache_region(image_path, search_region):
    """归一化区域语义作为缓存 key 的一部分(left/right/all 位置互不通用)"""
    if search_region is not None:
        return search_region
    is_item = "item" in image_path.replace("\\", "/").split("/")
    return "left" if is_item else "all"

def _smart_locate(image_path, confidence=0.86, timeout=10, min_conf=0.85, step=0.01, search_region=None):
    """查表优先定位：记录过 → 定点匹配（局部 ROI，快且稳）；
    定点未命中或未记录 → 全屏实时查找并刷新缓存。
    返回 (Point(含scale), 得分) 或 (None, 得分)。"""
    region = _cache_region(image_path, search_region)
    key = (image_path, region)
    cached = pos_cache.get(key)
    if cached is not None:
        pos, score = _locate_item_at(image_path, cached, scale=cached.scale)
        if pos is not None:
            pos_cache[key] = pos  # 逐轮微调, 吸收缓慢漂移
            return pos, score
        print(f"[查表] {image_path}[{region}] 定点未命中, 回退实时查找")

    pos, max_val = _locate_image(image_path, confidence=confidence, timeout=timeout,
                                 min_conf=min_conf, step=step, search_region=search_region)
    if pos is not None:
        pos_cache[key] = pos
        print(f"[查表] 记录 {image_path}[{region}] -> ({pos.x},{pos.y}) scale={getattr(pos, 'scale', None)}")
    else:
        pos_cache.pop(key, None)  # 位置失效则清缓存, 避免反复定点 miss
    return pos, max_val

def _perform_click(location):
    """直接点击坐标，跳过平滑移动过程"""
    if location:
        pyautogui.click(location.x, location.y)

def _perform_ctrl_click(location):
    """带 Ctrl 的点击"""
    if location:
        # 使用 hold 上下文管理器是最安全的方式
        with pyautogui.hold('ctrl'):
            pyautogui.click(location.x, location.y)

def click_image(image_path, confidence=0.86, timeout=10):
    ensure_not_stopped()
    location, max_val = _smart_locate(image_path, confidence, timeout)
    if location:
        _perform_click(location)
        time.sleep(0.1)
        print(f"成功点击 {image_path}")
        return True
    return False

def ctrl_click_image(image_path, confidence=0.86, timeout=10, search_region=None):
    ensure_not_stopped()
    location, max_val = _smart_locate(image_path, confidence, timeout, search_region=search_region)
    if location:
        # 确定实际生效的搜索区域类型，对点击坐标进行二次校验防护，防止边界重合时误操作
        effective_region = search_region
        if effective_region is None:
            is_item = "item" in image_path.replace("\\", "/").split("/")
            effective_region = "left" if is_item else "all"

        if matcher is not None:
            mid_x = matcher.mon_left + int(matcher.full_w * 0.58)
            if effective_region == "right" and location.x < mid_x:
                print(f"安全拦截: 虽匹配到 {image_path}，但坐标 {location.x} 位于左侧区域(X < 58%)，已忽略以防误进行[取出]操作")
                return None
            if effective_region == "left" and location.x >= mid_x:
                print(f"安全拦截: 虽匹配到 {image_path}，但坐标 {location.x} 位于右侧区域(X >= 58%)，已忽略以防误进行[放回]操作")
                return None

        _perform_ctrl_click(location)
        time.sleep(0.1)
        print(f"成功 Ctrl+点击 {image_path}")
        return location
    return None

def locationReconfirm(args):
    ensure_not_stopped()
    print("重置位置...")
    click_image('./public/move.png')
    # 鼠标移开防止遮挡图片 (移动到应用所在屏幕的 100, 100)
    mon_l = getattr(matcher, "mon_left", 0)
    mon_t = getattr(matcher, "mon_top", 0)
    pyautogui.moveTo(mon_l + 100, mon_t + 100)

    # 逻辑判断：已经在起点还是需要移动
    posA, max_valA = _locate_image(os.path.dirname(args.begin)+'/already_in_'+os.path.basename(args.begin), timeout=5)
    pos, max_val = _locate_image(args.begin, timeout=5)

    mva = max_valA if max_valA is not None else 0.0
    mv = max_val if max_val is not None else 0.0

    if max_valA is None or mva < mv:
        print(f"未在起点，开始移动到 {args.begin}")
        pyautogui.rightClick()
        move(args.begin)
    elif max_val is None or mva > mv:
        print(f"已在起点:{args.begin}")
        pyautogui.rightClick()

def findNeedItem(args):
    """寻找物品逻辑优化：在滚动时减小寻找等待时间"""
    if matcher is None: return False

    loc, max_val = _locate_image('./public/listHead.png')
    if not loc: return False

    target_w = getattr(matcher, "target_w", 2560)
    offset_x = int(800 * (target_w / 2560.0))
    pyautogui.moveTo(loc.x + offset_x, loc.y + 100)
    pyautogui.scroll(5000) # 先滚到最上面

    # 保证滚动动画彻底完成后再进行后续的代码执行
    print("等待列表滚动到顶并静止...")
    matcher.wait_for_idle(timeout=10)

    max_scroll = 15
    while max_scroll > 0:
        ensure_not_stopped()
        ret, max_val = _locate_image(args.item, timeout=10)
        print("findNeedItem got:", ret)
        if ret:
            print(f"找到物品: {args.item}")
            return True
        pyautogui.scroll(-115)
        max_scroll -= 1
        time.sleep(0.5) # 等待UI滚动动画
    return False

def move(image):
    ensure_not_stopped()
    click_image('./public/move.png')
    time.sleep(0.1)
    if click_image(image):
        time.sleep(0.1)
        if click_image('./public/accept.png'):
            time.sleep(2.5) # 移动等待不能省
            pyautogui.rightClick()

def getItem(image):
    ensure_not_stopped()
    return ctrl_click_image(image)

def put(image):
    ensure_not_stopped()
    return ctrl_click_image(image, search_region="right")

def _locate_item_at(image_path, target_pos, scale=None):
    """定点查找：在记录的 (位置, 尺度) 附近做带 mask 匹配 + 亮度门 + 颜色验证。

    仓库格子位置与物品渲染尺寸在任务期间固定，首次全屏命中后记录 scale，
    后续用本函数在局部 ROI 上匹配，避免重复全屏金字塔的开销。
    scale=None 时在金字塔全尺度内查找（用于首次定位的兜底）。
    返回 (Point(x,y,scale), 融合得分) 或 (None, 0.0)。
    """
    if matcher is None or target_pos is None:
        return None, 0.0

    target_data = matcher.load_image(image_path)
    if target_data is None:
        return None, 0.0
    target, mask = target_data
    h, w = target.shape[:2]

    is_item = "item" in image_path.replace("\\", "/").split("/")
    use_core = is_item and not getattr(matcher, "liquid_mode", False)
    if use_core:
        core_w = int(w * 0.50)
        core_h = int(h * 0.50)
        core_x = int((w - core_w) / 2)
        core_y = int((h - core_h) * 0.8)
    else:
        core_x, core_y, core_w, core_h = 0, 0, w, h

    core_target = target[core_y:core_y+core_h, core_x:core_x+core_w]
    core_mask = mask[core_y:core_y+core_h, core_x:core_x+core_w] if mask is not None else None
    _use_mask = core_mask is not None and not np.all(core_mask)

    hsv_core_target = cv2.cvtColor(core_target, cv2.COLOR_BGR2HSV)
    h1 = cv2.calcHist([hsv_core_target], [0, 1], core_mask, [30, 32], [0, 180, 0, 256])
    cv2.normalize(h1, h1)

    screen_img = matcher.grab_screen_img()
    img_h, img_w = screen_img.shape[:2]
    rel_cx = target_pos.x - matcher.rx
    rel_cy = target_pos.y - matcher.ry

    # 原屏 ROI 半径：覆盖物品最大渲染尺寸(模板×1/scale) + 位置误差冗余
    scales = [scale] if scale is not None else matcher.PYRAMID_SCALES
    roi_r = int(w * 0.5 / min(scales)) + 20
    x1 = max(0, rel_cx - roi_r)
    y1 = max(0, rel_cy - roi_r)
    x2 = min(img_w, rel_cx + roi_r)
    y2 = min(img_h, rel_cy + roi_r)
    if x2 - x1 < core_w or y2 - y1 < core_h:
        return None, 0.0
    roi_orig = screen_img[y1:y2, x1:x2]

    threshold = confidence_cache.get(image_path, 0.85)
    BRIGHTNESS_GATE = 60
    for s in scales:
        if abs(s - 1.0) < 1e-6:
            level = roi_orig
        else:
            sw = max(8, int(roi_orig.shape[1] * s))
            sh = max(8, int(roi_orig.shape[0] * s))
            level = cv2.resize(roi_orig, (sw, sh), interpolation=cv2.INTER_AREA)
        if level.shape[0] < core_h or level.shape[1] < core_w:
            continue

        res = cv2.matchTemplate(level, core_target, cv2.TM_CCOEFF_NORMED,
                                mask=core_mask if _use_mask else None)
        if _use_mask:
            res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val < threshold:
            continue

        # 亮度门
        core_roi = level[max_loc[1]:max_loc[1]+core_h, max_loc[0]:max_loc[0]+core_w]
        if core_roi.shape[:2] != (core_h, core_w):
            continue
        hsv_core_roi = cv2.cvtColor(core_roi, cv2.COLOR_BGR2HSV)
        if core_mask is not None:
            mean_target = cv2.mean(hsv_core_target[:, :, 2], mask=core_mask)[0]
            mean_roi    = cv2.mean(hsv_core_roi[:, :, 2], mask=core_mask)[0]
        else:
            mean_target = np.mean(hsv_core_target[:, :, 2])
            mean_roi    = np.mean(hsv_core_roi[:, :, 2])
        v_diff = abs(mean_target - mean_roi)
        if v_diff > BRIGHTNESS_GATE:
            print(f"  [定点 scale={s:.3f}] score={max_val:.3f} V差={v_diff:.1f} 超出亮度门")
            continue

        # 颜色直方图相关性
        h2 = cv2.calcHist([hsv_core_roi], [0, 1], core_mask, [30, 32], [0, 180, 0, 256])
        cv2.normalize(h2, h2)
        c_score = cv2.compareHist(h1, h2, cv2.HISTCMP_CORREL)
        final_score = 0.91 * max_val + 0.09 * max(0.0, c_score)

        # 层坐标还原原屏绝对坐标：物品在层上 = 模板 1:1，模板内偏移直接可用
        abs_x = int(x1 + (max_loc[0] - core_x + w // 2) / s) + matcher.rx
        abs_y = int(y1 + (max_loc[1] - core_y + h // 2) / s) + matcher.ry
        print(f"  [定点 scale={s:.3f}] core_score={max_val:.3f} V差={v_diff:.1f} color={c_score:.3f} final={final_score:.3f} ✓")
        return Point(abs_x, abs_y, s), float(final_score)

    return None, 0.0

def main(args):
    global matcher
    # 每次运行任务都实例化一个全新的 mss 内容上下文，防止 GDI 位图句柄在多线程重入时失效
    matcher = FastMatcher(getattr(args, 'resolution', '2560x1440'))
    matcher.liquid_mode = getattr(args, 'liquid_mode', False)

    # 新任务从零建表: 首轮全屏查找并记录各标签位置, 之后轮次查表定点
    pos_cache.clear()

    print("准备开始... 2秒倒计时")
    time.sleep(2)

    locationReconfirm(args)
    findNeedItem(args)

    for i in range(args.times):
        ensure_not_stopped()
        print(f"\n--- 循环 {i+1}/{args.times} ---")

        # getItem: 查表定点优先（首轮全屏建表, 后续轮局部 ROI 命中即点）
        pos = getItem(args.item)
        if pos is None:
            # 一次失败可能由 UI 动画/弹窗瞬态遮挡引起, 重试一次再判定耗尽
            print("未取到目标物品, 1 秒后重试...")
            time.sleep(1.0)
            pos = getItem(args.item)
            if pos is None:
                print("目标物品在起点仓库中已耗尽")
                break

        move(args.end)
        put(args.item)
        
        # 等待 UI 更新渲染完成
        time.sleep(0.5)
        
        # 检测背包中（分割后的右侧区域）是否仍然存在目标物品
        overflow_pos, _ = _smart_locate(args.item, search_region="right")
        if overflow_pos and overflow_pos.x >= matcher.mon_left + int(matcher.full_w * 0.58):
            print(f"检测到背包（右侧区域）中仍有目标物品 {args.item}，说明该物品任务已溢出！")
            print("正在将背包中多余的目标物品放回起点仓库中...")
            move(args.begin)
            
            # 循环放回所有多余的目标物品，限制最多 30 次尝试以防死循环
            put_back_count = 0
            while put_back_count < 30:
                ensure_not_stopped()
                pos_back, _ = _smart_locate(args.item, search_region="right")
                if not pos_back or pos_back.x < matcher.mon_left + int(matcher.full_w * 0.58):
                    break
                # 调用 put 放回，若点击失败则退出，防止死循环
                if not put(args.item):
                    break
                put_back_count += 1
                time.sleep(0.5)
            
            print(f"已将多余物品放回起点仓库，共放回 {put_back_count} 次。任务提前完成。")
            break
        
        move(args.begin)

    print("\n任务完成!")
    for img, conf in confidence_cache.items():
        print(f"  {img}: {conf:.2f}")


def run_transport(begin, end, item, times, resolution="2560x1440", liquid_mode=False):
    args = argparse.Namespace(begin=begin, end=end, item=item, times=times, resolution=resolution, liquid_mode=liquid_mode)
    main(args)

if __name__ == "__main__":
    reset_stop()
    args = get_args_parser().parse_args()
    main(args)
