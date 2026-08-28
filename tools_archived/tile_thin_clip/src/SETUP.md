# Tile-Thin-Clip — One-time Setup

This skill runs everything inside Firmatek's shared mmworkflow Docker image.
Per machine, you need Docker, AWS CLI, and ECR access.

## 1. Docker

Install Docker Desktop (Windows / Mac) or Docker Engine (Linux):
- https://www.docker.com/products/docker-desktop/

Verify:
```bash
docker info
```

## 2. AWS CLI + credentials

Install AWS CLI v2:
- https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html

Configure credentials with access to the `750433818015` ECR registry:
```bash
aws configure
```

Verify:
```bash
aws sts get-caller-identity
```

## 3. ECR login (per session — token expires after 12 h)

```bash
aws ecr get-login-password --region us-west-2 \
  | docker login --username AWS --password-stdin 750433818015.dkr.ecr.us-west-2.amazonaws.com
```

Login succeeds → pull the image (one-time, ~60 GB):
```bash
docker pull 750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:latest
```

The `run.sh` / `run.cmd` wrappers re-authenticate automatically when the
token has expired.

## 4. (Optional) GPU support

The tile-thin-clip pipeline is CPU-only and does not require a GPU. The
mmworkflow image bundles CUDA libraries, but this skill never invokes them.

---

## Environment variables

The wrappers honour these (all optional):

| Variable | Default | Purpose |
|----------|---------|---------|
| `TILE_THIN_CLIP_IMAGE` | `750433818015.dkr.ecr.us-west-2.amazonaws.com/mmworkflow:latest` | Override container image |
| `TILE_THIN_CLIP_REGION` | `us-west-2` | AWS region for ECR auth |

## Notes

- **Network shares**: Docker cannot mount network paths (UNC, mounted drive
  letters resolving to remote shares). Inputs must be on a local disk —
  stage data to `C:\`, `D:\`, `E:\`, `/home/...`, etc.
- **Image size**: ~60 GB on disk. Pull once per machine.
- **Token lifetime**: ECR login tokens are valid 12 h. The wrappers re-auth
  on demand; you can also re-run the login command manually if you hit
  `unauthorized` errors.
