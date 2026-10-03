# MEETIQ CI/CD Pipeline Documentation

This document describes the Continuous Integration and Deployment pipeline implemented for the **MEETIQ** platform using GitHub Actions.

---

## 1. CI Pipeline Overview

The CI pipeline workflow is defined in [`.github/workflows/ci.yml`](file:///c:/Users/DELL/OneDrive/Desktop/major_project/MEETIQ/.github/workflows/ci.yml). It automatically triggers on every `push` and `pull_request` to `main` and `develop` branches.

```text
GitHub Push / Pull Request Event
               │
               ▼
┌──────────────────────────────────────────────┐
│ Job 1: Backend Unit & Integration Tests     │
│ • Python 3.10 setup                          │
│ • System dependencies (ffmpeg, libgl1)       │
│ • Pytest test execution (python -m pytest)   │
└──────────────────────┬───────────────────────┘
                       │ (Passes)
                       ▼
┌──────────────────────────────────────────────┐
│ Job 2: Docker Build Validation               │
│ • Set up Docker Buildx                       │
│ • Verify Dockerfile compilation (no push)    │
└──────────────────────────────────────────────┘
```

---

## 2. Test Stages

1. **Python Environment Setup**: Configures Python 3.10 runtime environment with pip caching.
2. **System Dependencies**: Installs `ffmpeg`, `libgl1`, and `libglib2.0-0` required for Whisper audio processing and OpenCV vision tasks.
3. **Automated Unit & API Testing**: Executes pytest suite:
   ```bash
   python -m pytest backend/tests -v
   ```
4. **Hardening & Security Verification**: Ensures authentication, authorization, cross-meeting room isolation, file path traversal protection, and TURN configuration boundaries remain intact.

---

## 3. Docker Build

- Build validation runs as a secondary job after unit tests pass.
- Builds the production image (`docker build -t meetiq:ci-test .`) to verify Dockerfile syntax without pushing artifacts to external registries.

---

## 4. Secrets

- Unit and integration tests utilize mocked MongoDB (`mongomock`) and mocked AI services to avoid requiring live production database or API credentials during CI runs.
- Production environment secrets (`SECRET_KEY`, `MONGODB_URI`, `MAIL_PASSWORD`, `TURN_PASSWORD`) must be supplied via GitHub Repository Secrets for deployment jobs.

---

## 5. Branch Workflow

- **`main`**: Protected production branch. All pull requests require clean CI pass before merging.
- **`develop`**: Integration branch for upcoming milestone features.

---

## 6. Failure Handling

- If any unit test fails or Dockerfile syntax is invalid, the workflow immediately fails and notifies developers on the pull request.
- Failed workflow runs block code merge until issues are resolved.
