# stt_pretrained.py — Stub implementation
# TODO: Implement with actual Indic STT model


class IndianSTT:
    """Stub Speech-to-Text class for Indian languages"""

    def __init__(self, language='hindi'):
        self.language = language

    def transcribe(self, audio_path):
        """Transcribe audio file to text.

        Args:
            audio_path: Path to audio file.

        Returns:
            Transcribed text string.
        """
        raise NotImplementedError(
            "IndianSTT is a placeholder. "
            "Implement this class with an actual STT model "
            "(e.g., Whisper, IndicWav2Vec) to enable transcription."
        )
