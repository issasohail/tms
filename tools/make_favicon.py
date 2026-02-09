from PIL import Image
from pathlib import Path

base = Path(r"C:\tenant_management_system\static\images")
src = base / "logo.png"  # your existing file
dst_ico = base / "favicon.ico"
dst_32 = base / "favicon-32x32.png"
dst_16 = base / "favicon-16x16.png"
dst_apple = base / "apple-touch-icon.png"

img = Image.open(src).convert("RGBA")

# ensure square: letterbox/pad to square using transparent background
size = max(img.width, img.height)
canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
canvas.paste(img, ((size - img.width)//2, (size - img.height)//2))

# export ICO with multiple sizes
canvas.save(dst_ico, format="ICO",
            sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])

# handy PNG sizes
canvas.resize((32, 32), Image.LANCZOS).save(dst_32)
canvas.resize((16, 16), Image.LANCZOS).save(dst_16)
canvas.resize((180, 180), Image.LANCZOS).save(dst_apple)
print("Favicon files written to:", base)
