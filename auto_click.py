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
pyautogui.PAUSE = 0.1  # 去掉 pyautogui 默认的 0.1s 延迟
Point = namedtuple('Point', ['x', 'y']) # 模拟 pyautogui 的坐标对象

# 全局字典，保存每个图片的最佳置信度
confidence_cache = {}
# 图片对象缓存，避免重复读取硬盘
image_obj_cache = {}

class FastMatcher:
    """使用 mss + OpenCV 的高速匹配类"""
    def __init__(self):
        self.sct = mss.mss()
        monitor = self.sct.monitors[1]
        full_w = monitor["width"]
        full_h = monitor["height"]
        self.full_w = full_w

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

        # 获取图片高度和宽度
        h, w = img.shape[:2]

        # 如果是物品图片 (item 文件夹)，则对底部进行遮罩
        # 施加一个遮罩，自动裁剪从底部开始，高度占全图 25% 的内容，使其透明化
        is_item = "item" in path.replace("\\", "/").split("/")
        if is_item:
            # 如果是 3 通道的图，先转为 4 通道（带 alpha）
            if img.shape[2] == 3:
                alpha_channel = np.ones((h, w), dtype=np.uint8) * 255
                img = cv2.merge((img, alpha_channel))
            
            # 将底部 25% 的区域 alpha 设为 0
            mask_h = int(h * 0.75)
            img[mask_h:, :, 3] = 0

        # 如果有 alpha 通道（或者刚才为了遮罩新生成的）
        if img.shape[2] == 4:
            bgr = img[:, :, :3]
            alpha = img[:, :, 3]

            # 找到非透明区域（alpha > 0）
            coords = np.column_stack(np.where(alpha > 0))
            if coords.size > 0:
                y_min, x_min = coords.min(axis=0)
                y_max, x_max = coords.max(axis=0)

                # 按照非透明区域进行最终裁剪
                bgr = bgr[y_min:y_max+1, x_min:x_max+1]

            else:
                # 全透明，直接返回 None（避免后续匹配异常）
                print(f"警告: 图片全透明 {path}")
                return None

            image_obj_cache[path] = bgr
            return bgr

        else:
            # 没有 alpha（jpg等），直接用
            image_obj_cache[path] = img
            return img

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
            
            # 如果变化面积小于总面积的 0.1%，认为大面积滚动/动画已停止
            if change_ratio < tolerance:
                print(f"画面静止，继续执行后续操作, 耗时: {time.time() - start_time}")
                return True
                
            last_img = curr_img
        return False

    def locate_on_screen(self, screen_img, image_path):
        target = self.load_image(image_path)
        if target is None:
            return None, 0.0

        # === 1. 结构匹配（原始 matchTemplate）===
        res = cv2.matchTemplate(screen_img, target, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)

        h, w = target.shape[:2]
        x = max_loc[0]
        y = max_loc[1]

        # === 2. 取 ROI 做颜色匹配 ===
        roi = screen_img[y:y+h, x:x+w]
        if roi.shape[:2] != target.shape[:2]:
            return None, 0.0

        # === 3. 颜色相似度（HSV直方图）===
        def color_score(img1, img2):
            hsv1 = cv2.cvtColor(img1, cv2.COLOR_BGR2HSV)
            hsv2 = cv2.cvtColor(img2, cv2.COLOR_BGR2HSV)

            hist1 = cv2.calcHist([hsv1], [0,1], None, [30,32], [0,180,0,256])
            hist2 = cv2.calcHist([hsv2], [0,1], None, [30,32], [0,180,0,256])

            cv2.normalize(hist1, hist1)
            cv2.normalize(hist2, hist2)

            return cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)

        c_score = color_score(target, roi)

        # === 4. 融合（关键）===
        # alpha：结构权重，beta：颜色权重
        alpha = 1
        beta = 0.1

        final_score = alpha * max_val + beta * c_score

        # === 5. 返回 ===
        abs_x = x + w // 2 + self.rx
        abs_y = y + h // 2 + self.ry

        return Point(abs_x, abs_y), float(final_score)


# 初始化匹配器 (延迟到任务开始时在主逻辑中初始化)
matcher: FastMatcher | None = None
stop_event = threading.Event()

def get_args_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("-t", "--times", type=int, default=30, help='执行次数')
    parser.add_argument("-e", "--end", type=str, default='./region/wuling.png', help='终点')
    parser.add_argument("-b", "--begin", type=str, default='./region/forth_valley.png', help='起点')
    parser.add_argument("-i", "--item", help='物品')
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

def _locate_image(image_path, confidence=0.86, timeout=10, min_conf=0.8, step=0.02):
    global call_id
    if matcher is None:
        print("错误: matcher 未初始化")
        return None, 0.0
        
    call_id += 1
    cid = call_id

    current_confidence = confidence_cache.get(image_path, confidence)
    screen_img = matcher.grab_screen_img()
    pos, max_val = matcher.locate_on_screen(screen_img, image_path)

    # 打印日志
    val_display = f"{max_val:.3f}" if max_val is not None else "0.000"
    print(f"[{cid}] {image_path} score={val_display} init_thr={current_confidence:.2f} min_thr={min_conf:.2f} pos={'Y' if pos else 'N'}")

    if not pos or max_val is None:
        print(f"[{cid}] RETURN None (no pos)")
        return None, float(max_val or 0.0)

    # 对于 item 的最终 location 必须在屏幕靠左的 55% 的范围内，否则无效
    is_item = "item" in image_path.replace("\\", "/").split("/")
    if is_item:
        limit_x = matcher.full_w * 0.58
        if pos.x > limit_x:
            print(f"[{cid}] 警告: 物品由于超过屏幕左侧 58% 区域 (x={pos.x:.1f} > limit={limit_x:.1f}) 被过滤")
            return None, float(max_val)

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

def ctrl_click_image(image_path, confidence=0.86, timeout=10):
    ensure_not_stopped()
    location, max_val = _locate_image(image_path, confidence, timeout)
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
    
    pyautogui.moveTo(loc.x + 800, loc.y + 100)
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

def put():
    ensure_not_stopped()
    ctrl_click_image('./public/put.png')

def main(args):
    global matcher
    # 每次运行任务都实例化一个全新的 mss 内容上下文，防止 GDI 位图句柄在多线程重入时失效
    matcher = FastMatcher()
    
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
        put()
        move(args.begin)
        
    print("\n任务完成!")
    for img, conf in confidence_cache.items():
        print(f"  {img}: {conf:.2f}")


def run_transport(begin, end, item, times):
    args = argparse.Namespace(begin=begin, end=end, item=item, times=times)
    main(args)

if __name__ == "__main__":
    args = get_args_parser().parse_args()
    main(args)
