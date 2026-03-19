from PIL import Image

img = Image.open("input.png")

img.save(
    "app.ico",
    format="ICO",
    sizes=[(16,16), (32,32), (48,48), (64,64), (128,128), (256,256)]
)