__all__ = ["CDGLyricsExtractor"]


def __getattr__(name: str):
    if name == "CDGLyricsExtractor":
        from .extractor import CDGLyricsExtractor

        return CDGLyricsExtractor

    raise AttributeError(name)
