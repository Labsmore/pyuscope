from PIL import Image

def get_scaled(image, factor, filt=Image.NEAREST):
    return image.resize((int(image.size[0] * factor), int(image.size[1] * factor)), filt)
