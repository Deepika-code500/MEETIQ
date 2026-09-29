import cv2
import mediapipe as mp
import math
from collections import deque

# --------------------------------------------------
# MediaPipe setup
# --------------------------------------------------

BaseOptions = mp.tasks.BaseOptions
FaceLandmarker = mp.tasks.vision.FaceLandmarker
FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode

MODEL_PATH = "face_landmarker_v2_with_blendshapes.task"


# --------------------------------------------------
# Helper function
# --------------------------------------------------

def distance(p1, p2):
    return math.sqrt(
        (p1.x - p2.x) ** 2 +
        (p1.y - p2.y) ** 2
    )


# --------------------------------------------------
# Eye Aspect Ratio
# --------------------------------------------------

def calculate_ear(landmarks, points):

    p1 = landmarks[points[0]]
    p2 = landmarks[points[1]]
    p3 = landmarks[points[2]]
    p4 = landmarks[points[3]]
    p5 = landmarks[points[4]]
    p6 = landmarks[points[5]]

    vertical_1 = distance(p2, p6)
    vertical_2 = distance(p3, p5)
    horizontal = distance(p1, p4)

    if horizontal == 0:
        return 0

    return (vertical_1 + vertical_2) / (2.0 * horizontal)


# --------------------------------------------------
# Camera
# --------------------------------------------------

cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("ERROR: Could not open webcam.")
    exit()


# --------------------------------------------------
# Face Landmarker
# --------------------------------------------------

options = FaceLandmarkerOptions(
    base_options=BaseOptions(
        model_asset_path=MODEL_PATH
    ),
    running_mode=VisionRunningMode.IMAGE,
    num_faces=1
)


# --------------------------------------------------
# Smoothing buffers
# --------------------------------------------------

eye_history = deque(maxlen=5)
head_history = deque(maxlen=5)


# --------------------------------------------------
# Start monitoring
# --------------------------------------------------

with FaceLandmarker.create_from_options(options) as landmarker:

    print("======================================")
    print("MeetIQ Attention Monitoring")
    print("Eye Tracking + Head Tracking")
    print("Press Q to exit")
    print("======================================")

    while True:

        success, frame = cap.read()

        if not success:
            print("ERROR: Could not read webcam.")
            break

        frame = cv2.flip(frame, 1)

        height, width, _ = frame.shape

        # Convert image
        rgb_frame = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2RGB
        )

        mp_image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=rgb_frame
        )

        # Detect face
        result = landmarker.detect(mp_image)


        # ==================================================
        # NO FACE
        # ==================================================

        if not result.face_landmarks:

            score = 0
            status = "NOT ATTENTIVE"

            eye_history.clear()
            head_history.clear()

            cv2.putText(
                frame,
                "Attention Score: 0%",
                (30, 50),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 0, 255),
                2
            )

            cv2.putText(
                frame,
                status,
                (30, 85),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 0, 255),
                2
            )


        # ==================================================
        # FACE DETECTED
        # ==================================================

        else:

            landmarks = result.face_landmarks[0]


            # --------------------------------------------------
            # Face bounding box
            # --------------------------------------------------

            x_values = [
                int(p.x * width)
                for p in landmarks
            ]

            y_values = [
                int(p.y * height)
                for p in landmarks
            ]

            x_min = max(0, min(x_values))
            x_max = min(width, max(x_values))

            y_min = max(0, min(y_values))
            y_max = min(height, max(y_values))

            cv2.rectangle(
                frame,
                (x_min, y_min),
                (x_max, y_max),
                (0, 255, 0),
                2
            )


            # ==================================================
            # HEAD TRACKING
            # ==================================================

            nose = landmarks[1]

            face_center_x = (x_min + x_max) / 2
            face_center_y = (y_min + y_max) / 2

            nose_x = nose.x * width
            nose_y = nose.y * height

            face_width = max(x_max - x_min, 1)
            face_height = max(y_max - y_min, 1)

            horizontal_ratio = (
                abs(nose_x - face_center_x)
                / face_width
            )

            vertical_ratio = (
                abs(nose_y - face_center_y)
                / face_height
            )

            head_facing_camera = (
                horizontal_ratio < 0.18
                and
                vertical_ratio < 0.20
            )

            head_history.append(head_facing_camera)

            head_facing_stable = (
                sum(head_history) >=
                len(head_history) * 0.6
            )


            # ==================================================
            # EYE TRACKING - EAR
            # ==================================================

            # Left eye
            left_eye_points = [
                33,    # outer
                159,   # upper
                158,   # upper
                133,   # inner
                153,   # lower
                145    # lower
            ]

            # Right eye
            right_eye_points = [
                362,   # outer
                386,   # upper
                385,   # upper
                263,   # inner
                373,   # lower
                374    # lower
            ]

            left_ear = calculate_ear(
                landmarks,
                left_eye_points
            )

            right_ear = calculate_ear(
                landmarks,
                right_eye_points
            )

            average_ear = (
                left_ear + right_ear
            ) / 2


            # --------------------------------------------------
            # Eye threshold
            #
            # Below this = likely closed
            # --------------------------------------------------

            eyes_open = average_ear > 0.18

            eye_history.append(eyes_open)

            eyes_open_stable = (
                sum(eye_history) >=
                len(eye_history) * 0.6
            )


            # ==================================================
            # FINAL ATTENTION SCORE
            # ==================================================

            if (
                head_facing_stable
                and
                eyes_open_stable
            ):

                score = 100
                status = "ATTENTIVE"

            else:

                score = 75
                status = "PARTIALLY ATTENTIVE"


            # ==================================================
            # Display
            # ==================================================

            cv2.putText(
                frame,
                f"Attention Score: {score}%",
                (30, 50),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2
            )

            cv2.putText(
                frame,
                status,
                (30, 85),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2
            )

            # Head status
            if head_facing_stable:
                head_text = "HEAD: FACING CAMERA"
            else:
                head_text = "HEAD: TURNED"

            # Eye status
            if eyes_open_stable:
                eye_text = "EYES: OPEN"
            else:
                eye_text = "EYES: CLOSED"

            cv2.putText(
                frame,
                head_text,
                (30, 120),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 0),
                2
            )

            cv2.putText(
                frame,
                eye_text,
                (30, 150),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 0),
                2
            )

            # Show EAR value for testing
            cv2.putText(
                frame,
                f"EAR: {average_ear:.2f}",
                (30, 180),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 0),
                2
            )


            # ==================================================
            # Draw landmarks
            # ==================================================

            for landmark in landmarks:

                x = int(landmark.x * width)
                y = int(landmark.y * height)

                cv2.circle(
                    frame,
                    (x, y),
                    1,
                    (0, 255, 0),
                    -1
                )


        # --------------------------------------------------
        # Show window
        # --------------------------------------------------

        cv2.imshow(
            "MeetIQ - AI Attention Monitoring",
            frame
        )

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break


# --------------------------------------------------
# Cleanup
# --------------------------------------------------

cap.release()
cv2.destroyAllWindows()

print("Attention Monitoring Stopped.")