def check() -> None:
    text = "hello world"
    max_words: int = 5
    words = text.split()
    text = " ".join(words[:max_words])
