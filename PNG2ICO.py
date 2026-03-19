from PIL import Image

img = Image.open("input.png")

sizes = [(16,16),(20,20),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)]

icons = []
for size in sizes:
    resized = img.resize(size, Image.LANCZOS)
    icons.append(resized)

img.save("app.ico", sizes=sizes)