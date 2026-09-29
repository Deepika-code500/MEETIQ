import librosa
import numpy as np
from detoxify import Detoxify


# =========================================================
# TOXICITY MODEL
# =========================================================

model = Detoxify("original")


# =========================================================
# TEXT TOXICITY
# =========================================================

def detect_text_toxicity(text):

    if not text or not text.strip():

        return {
            "toxicity": 0.0,
            "severe_toxicity": 0.0,
            "obscene": 0.0,
            "threat": 0.0,
            "insult": 0.0,
            "identity_attack": 0.0
        }

    results = model.predict(text)

    return {
        "toxicity": float(
            results.get("toxicity", 0)
        ),

        "severe_toxicity": float(
            results.get("severe_toxicity", 0)
        ),

        "obscene": float(
            results.get("obscene", 0)
        ),

        "threat": float(
            results.get("threat", 0)
        ),

        "insult": float(
            results.get("insult", 0)
        ),

        "identity_attack": float(
            results.get("identity_attack", 0)
        )
    }


# =========================================================
# VOICE ANALYSIS
# =========================================================

def analyze_voice(audio_file):

    try:

        # Load audio
        audio, sample_rate = librosa.load(
            audio_file,
            sr=None,
            mono=True
        )

        if len(audio) == 0:

            return {
                "average_pitch": 0.0,
                "average_energy": 0.0,
                "speech_duration": 0.0
            }


        # -------------------------------------------------
        # PITCH
        # -------------------------------------------------

        pitch = librosa.yin(
            audio,
            fmin=50,
            fmax=500,
            sr=sample_rate
        )

        valid_pitch = pitch[
            np.isfinite(pitch)
        ]

        if len(valid_pitch) > 0:

            average_pitch = float(
                np.mean(valid_pitch)
            )

        else:

            average_pitch = 0.0


        # -------------------------------------------------
        # ENERGY
        # -------------------------------------------------

        rms_energy = librosa.feature.rms(
            y=audio
        )[0]

        average_energy = float(
            np.mean(rms_energy)
        )


        # -------------------------------------------------
        # DURATION
        # -------------------------------------------------

        speech_duration = float(
            len(audio) / sample_rate
        )


        return {
            "average_pitch": round(
                average_pitch,
                2
            ),

            "average_energy": round(
                average_energy,
                4
            ),

            "speech_duration": round(
                speech_duration,
                2
            )
        }


    except Exception as error:

        print(
            "Voice analysis error:",
            error
        )

        return {
            "average_pitch": 0.0,
            "average_energy": 0.0,
            "speech_duration": 0.0
        }

def moderation_decision(toxicity_score, previous_toxic_events=0):
    """
    Decide what moderation action should be taken.

    toxicity_score:
        Overall toxicity score from Detoxify (0.0 to 1.0)

    previous_toxic_events:
        Number of previous toxic detections for this participant
        during the meeting.
    """

    if toxicity_score < 0.60:
        return {
            "action": "none",
            "message": "No toxicity detected."
        }

    if toxicity_score < 0.85:
        return {
            "action": "warning",
            "message": "Please keep the conversation respectful."
        }

    if previous_toxic_events == 0:
        return {
            "action": "warning",
            "message": "Warning: Please keep the conversation respectful."
        }

    if previous_toxic_events == 1:
        return {
            "action": "warning",
            "message": "Second warning: Continued inappropriate language may result in your microphone being muted."
        }

    return {
        "action": "mute",
        "message": "Your microphone has been muted due to repeated inappropriate language."
    }
def analyze_audio_for_toxicity(audio_file):
    """
    Analyze an audio file using Whisper for transcription,
    Detoxify for text toxicity, and Librosa for voice features.
    """

    import whisper

    print("\nLoading Whisper model...")
    whisper_model = whisper.load_model("base")

    print("Transcribing audio...")
    result = whisper_model.transcribe(audio_file)

    transcript = result["text"].strip()

    print("\nTranscript:")
    print(transcript)

    toxicity = detect_text_toxicity(transcript)

    voice = analyze_voice(audio_file)

    return {
        "transcript": transcript,
        "toxicity": toxicity,
        "voice": voice
    }
# =========================================================
# TEST
# =========================================================

if __name__ == "__main__":

    print("MeetIQ Toxicity Detection Module")

    audio_file = r"..\speech-to-text\recordings\Recording.m4a"

    result = analyze_audio_for_toxicity(audio_file)

    print("\n==============================")
    print("TOXICITY ANALYSIS RESULT")
    print("==============================")

    print("\nTranscript:")
    print(result["transcript"])

    print("\nToxicity Scores:")
    for category, score in result["toxicity"].items():
        print(f"{category}: {score:.4f}")

    print("\nVoice Analysis:")
    print(
        f"Average Pitch: "
        f"{result['voice']['average_pitch']} Hz"
    )

    print(
        f"Average Energy: "
        f"{result['voice']['average_energy']}"
    )

    print(
        f"Speech Duration: "
        f"{result['voice']['speech_duration']} seconds"
    )