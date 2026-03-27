from typing import Set

def test_slice(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) > max_words * 2:
        text = " ".join(words[0:max_words])
        last_punct = -1
        # finding last punctuation manually to bypass static analyzer bug with str.rfind
        for i in range(len(text) - 1, int(len(text) * 0.5), -1):
            if text[i] in {'.', '!', '?', ','}:
                last_punct = i
                break
        
        if last_punct != -1:
            text = text[:last_punct + 1]
    return text
