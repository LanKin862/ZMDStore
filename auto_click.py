import pyautogui
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
Point = namedtuple('Point', ['x', 'y']) # 模拟 pyautogui 的坐标对象

# 全局字典，保存每个图片的最佳置信度
confidence_cache = {}
# 图片对象缓存，避免重复读取硬盘
image_obj_cache = {}

class FastMatcher:
    """使用 mss + OpenCV 的高速匹配类"""
    def __init__(self, resolution="2560x1440"):
        self.sct = mss.mss()
        monitor = self.sct.monitors[1]
        full_w = monitor["width"]
        full_h = monitor["height"]
        self.full_w = full_w

        # 解析用户选择的分辨率，计算缩放因子
        res_parts = resolution.split('x')
        self.target_w = int(res_parts[0]) if len(res_parts) == 2 else 2560
        self.target_h = int(res_parts[1]) if len(res_parts) == 2 else 1440
        self.scale_factor = self.target_w / 2560.0

        # 1. 基础尺寸（原始的 76%x58%）
        self.rw = int(full_w * 0.76)
        base_rh = int(full_h * 0.58)
        
        # 2. 原始居中时的偏移量
        self.rx = int((full_w - self.rw) / 2)
        base_ry = int((full_h - base_rh) / 2)
        
        # 3. 上边增高 100 像素
        # 顶部偏移量减少 100 (向上延伸)
        self.ry = max(0, base_ry - 100) 
        # 总高度增加 200
        self.rh = base_rh + 200 

        # 构造搜索区域
        self.search_region = {
            "top": self.ry,
            "left": self.rx,
            "width": self.rw,
            "height": self.rh
        }
        print(f"区域调整：高度增加100px，当前区域 {self.rw}x{self.rh}，偏移({self.rx}, {self.ry})")

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

        # 提前清理透明背景的杂色（Alpha 预乘）
        if len(img.shape) == 3 and img.shape[2] == 4:
            img[:, :, :3] = (img[:, :, :3].astype(float) * (img[:, :, 3:].astype(float) / 255.0)).astype(np.uint8)

        # 强制检查并缩放 item 图片：基准为 2560x1440 屏幕下对应 128x128 的模版
        if is_item:
            target_w = getattr(self, "target_w", 2560)
            target_h = getattr(self, "target_h", 1440)
            target_item_w = int(128 * (target_w / 2560.0))
            target_item_h = int(128 * (target_h / 1440.0))
            
            if img.shape[1] != target_item_w or img.shape[0] != target_item_h:
                img = cv2.resize(img, (target_item_w, target_item_h), interpolation=cv2.INTER_AREA)

        # === 临时调试: 输出图片到 temp 文件夹 ===
        if is_item:
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

    def locate_on_screen(self, screen_img, image_path, search_region="all"):
        target_data = self.load_image(image_path)
        if target_data is None:
            return None, 0.0
        target, mask = target_data

        is_item = "item" in image_path.replace("\\", "/").split("/")

        h, w = target.shape[:2]
        BRIGHTNESS_GATE = 60
        # 候选搜索参数：最多尝试 N 个候选位置，低于下限分直接停止
        MAX_CANDIDATES = 8
        MIN_CANDIDATE_VAL = 0.75  # 降低下限，容忍细节差异导致的基础低分

        hsv_target = cv2.cvtColor(target, cv2.COLOR_BGR2HSV)

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
            
        # core_x = 0
        # core_y = 0
        # core_w = w
        # core_h = h
        core_target = target[core_y:core_y+core_h, core_x:core_x+core_w]

        # === 临时调试: 输出核心区域图片到 temp 文件夹 ===
        if is_item:
            try:
                temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
                os.makedirs(temp_dir, exist_ok=True)
                base_name = os.path.basename(image_path)
                name, ext = os.path.splitext(base_name)
                debug_path = os.path.join(temp_dir, f"core_{name}.png")
                _, encoded_img = cv2.imencode(".png", core_target)
                encoded_img.tofile(debug_path)
            except Exception as e:
                print(f"[Debug] 调试核心图片保存失败 {image_path}: {e}")
        # === 调试结束 ===

        res = cv2.matchTemplate(screen_img, core_target, cv2.TM_CCOEFF_NORMED)
        res_copy = res.copy()

        # 应用左右区域限制（在 NMS 之前直接过滤掉不属于该区域的位置）
        if search_region == "left":
            limit_x_in_roi = int(self.full_w * 0.58 - self.rx - core_w // 2)
            if 0 < limit_x_in_roi < res_copy.shape[1]:
                res_copy[:, limit_x_in_roi:] = -1
        elif search_region == "right":
            limit_x_in_roi = int(self.full_w * 0.58 - self.rx - core_w // 2)
            if 0 < limit_x_in_roi < res_copy.shape[1]:
                res_copy[:, :limit_x_in_roi] = -1

        # 多候选 NMS 迭代
        for attempt in range(MAX_CANDIDATES):
            _, max_val, _, max_loc = cv2.minMaxLoc(res_copy)

            if max_val < MIN_CANDIDATE_VAL:
                print(f"  [候选{attempt}] core_score={max_val:.3f} < {MIN_CANDIDATE_VAL}，停止搜索")
                break

            # 将核心区域的匹配坐标还原为完整模板的左上角坐标
            x = max_loc[0] - core_x
            y = max_loc[1] - core_y
            
            # 压制当前找到的核心区域（防止死循环）
            yc, xc = max_loc[1], max_loc[0]
            y1 = max(0, yc - core_h // 2)
            y2 = min(res_copy.shape[0], yc + core_h // 2 + 1)
            x1 = max(0, xc - core_w // 2)
            x2 = min(res_copy.shape[1], xc + core_w // 2 + 1)
            res_copy[y1:y2, x1:x2] = -1

            # 检查还原后的完整区域是否越界
            if x < 0 or y < 0 or x + w > screen_img.shape[1] or y + h > screen_img.shape[0]:
                continue

            # 提取与核心区域精准匹配的 ROI
            core_roi = screen_img[max_loc[1]:max_loc[1]+core_h, max_loc[0]:max_loc[0]+core_w]
            if core_roi.shape[:2] != (core_h, core_w):
                continue

            # 准备核心区域的颜色计算数据
            core_mask = mask[core_y:core_y+core_h, core_x:core_x+core_w] if mask is not None else None
            hsv_core_target = cv2.cvtColor(core_target, cv2.COLOR_BGR2HSV)
            hsv_core_roi = cv2.cvtColor(core_roi, cv2.COLOR_BGR2HSV)

            # 亮度过滤门（仅在核心区域判定）
            if core_mask is not None:
                mean_target = cv2.mean(hsv_core_target[:, :, 2], mask=core_mask)[0]
                mean_roi    = cv2.mean(hsv_core_roi[:, :, 2], mask=core_mask)[0]
            else:
                mean_target = np.mean(hsv_core_target[:, :, 2])
                mean_roi    = np.mean(hsv_core_roi[:, :, 2])
                
            v_diff = abs(mean_target - mean_roi)
            if v_diff > BRIGHTNESS_GATE:
                print(f"  [候选{attempt}] score={max_val:.3f} V差={v_diff:.1f} 超出亮度门，已压制并跳过")
                continue

            # 通过亮度门：计算颜色辅助分（仅在核心区域计算，彻底无视边缘的抗锯齿杂色）
            h1 = cv2.calcHist([hsv_core_target], [0, 1], core_mask, [30, 32], [0, 180, 0, 256])
            h2 = cv2.calcHist([hsv_core_roi],    [0, 1], core_mask, [30, 32], [0, 180, 0, 256])
            cv2.normalize(h1, h1)
            cv2.normalize(h2, h2)
            c_score = cv2.compareHist(h1, h2, cv2.HISTCMP_CORREL)

            # 融合得分
            alpha, beta = 0.91, 0.09
            
            # 如果颜色相关性为负数（完全不匹配），将其截断为 0
            c_score_clamped = max(0.0, c_score)
            
            final_score = alpha * max_val + beta * c_score_clamped

            print(f"  [候选{attempt}] core_score={max_val:.3f} V差={v_diff:.1f} color={c_score:.3f} final={final_score:.3f} ✓")

            abs_x = x + w // 2 + self.rx
            abs_y = y + h // 2 + self.ry
            return Point(abs_x, abs_y), float(final_score)

        return None, 0.0


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

def _locate_image(image_path, confidence=0.86, timeout=10, min_conf=0.86, step=0.01, search_region=None):
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
    location, max_val = _locate_image(image_path, confidence, timeout)
    if location:
        _perform_click(location)
        time.sleep(0.1)
        print(f"成功点击 {image_path}")
        return True
    return False

def ctrl_click_image(image_path, confidence=0.86, timeout=10, search_region=None):
    ensure_not_stopped()
    location, max_val = _locate_image(image_path, confidence, timeout, search_region=search_region)
    if location:
        _perform_ctrl_click(location)
        time.sleep(0.1)
        print(f"成功 Ctrl+点击 {image_path}")
        return True
    return False

def locationReconfirm(args):
    ensure_not_stopped()
    print("重置位置...")
    click_image('./public/move.png')
    # 鼠标移开防止遮挡图片
    pyautogui.moveTo(100, 100) 
    
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
    ctrl_click_image(image)

def put(image):
    ensure_not_stopped()
    ctrl_click_image(image, search_region="right")

def main(args):
    global matcher
    # 每次运行任务都实例化一个全新的 mss 内容上下文，防止 GDI 位图句柄在多线程重入时失效
    matcher = FastMatcher(getattr(args, 'resolution', '2560x1440'))
    matcher.liquid_mode = getattr(args, 'liquid_mode', False)
    
    reset_stop()
    print("准备开始... 2秒倒计时")
    time.sleep(2)

    locationReconfirm(args)
    findNeedItem(args)
    
    for i in range(args.times):
        ensure_not_stopped()
        print(f"\n--- 循环 {i+1}/{args.times} ---")
        getItem(args.item)
        move(args.end)
        put(args.item)
        move(args.begin)
        
    print("\n任务完成!")
    for img, conf in confidence_cache.items():
        print(f"  {img}: {conf:.2f}")


def run_transport(begin, end, item, times, resolution="2560x1440", liquid_mode=False):
    args = argparse.Namespace(begin=begin, end=end, item=item, times=times, resolution=resolution, liquid_mode=liquid_mode)
    main(args)

if __name__ == "__main__":
    args = get_args_parser().parse_args()
    main(args)
