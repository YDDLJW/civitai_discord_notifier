try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None
    ImageDraw = None

try:
    import pystray
except ImportError:
    pystray = None

from config import resolve_icon_path


class TrayManager:
    def __init__(self, icon_path_getter, logger, on_open, on_close):
        self.icon_path_getter = icon_path_getter
        self.logger = logger
        self.on_open = on_open
        self.on_close = on_close
        self.tray_icon = None

    def available(self):
        return pystray is not None and Image is not None

    def create_image(self):
        icon_path = resolve_icon_path(self.icon_path_getter())
        if icon_path.exists() and Image is not None:
            try:
                img = Image.open(icon_path)
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                return img
            except Exception:
                pass

        if Image is None:
            return None

        image = Image.new("RGBA", (64, 64), (32, 32, 32, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill=(80, 120, 255, 255))
        draw.text((18, 18), "CN", fill=(255, 255, 255, 255))
        return image

    def start(self):
        if not self.available():
            return False

        if self.tray_icon is not None:
            return True

        image = self.create_image()
        if image is None:
            return False

        menu = pystray.Menu(
            pystray.MenuItem("Open Window / 打开窗口", lambda icon, item: self.on_open(), default=True),
            pystray.MenuItem("Close / 关闭", lambda icon, item: self.on_close()),
        )

        try:
            self.tray_icon = pystray.Icon("civitai_notifier", image, "Civitai Notifier", menu)
            self.tray_icon.run_detached()
            return True
        except Exception as e:
            self.logger(f"托盘启动失败: {e}")
            self.tray_icon = None
            return False

    def restart(self):
        old = self.tray_icon
        self.tray_icon = None
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
        return self.start()

    def stop(self):
        old = self.tray_icon
        self.tray_icon = None
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass

    def notify(self, title, message):
        if self.start() and self.tray_icon is not None:
            try:
                self.tray_icon.notify(message, title)
                return True
            except Exception as e:
                self.logger(f"托盘通知失败: {e}")
        return False