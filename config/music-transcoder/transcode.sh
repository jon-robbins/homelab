#!/bin/bash
set -euo pipefail

MUSIC_DIR="${MUSIC_DIR:-/music}"
BITRATE="${BITRATE:-320k}"
SCAN_INTERVAL="${SCAN_INTERVAL:-300}"
LIDARR_URL="${LIDARR_URL:-http://lidarr:8686/lidarr}"
API_KEY_FILE="${API_KEY_FILE:-/run/secrets/LIDARR_API_KEY}"

echo "[transcoder] Starting music-transcoder worker..."
echo "[transcoder] Monitoring ${MUSIC_DIR} (Bitrate: ${BITRATE}, Interval: ${SCAN_INTERVAL}s)"

run_pass() {
  local converted_folders=()

  if [ ! -d "${MUSIC_DIR}" ]; then
    echo "[transcoder] Directory ${MUSIC_DIR} does not exist, skipping..."
    return 0
  fi

  # Find all .flac files (null-delimited to handle spaces/special characters)
  while IFS= read -r -d '' flac_path; do
    local dir
    dir="$(dirname "${flac_path}")"
    local base
    base="$(basename "${flac_path}" .flac)"
    local target_mp3="${dir}/${base}.mp3"
    local tmp_mp3="${dir}/${base}.mp3.tmp"

    echo "[transcoder] Processing: ${flac_path}"

    # If target mp3 already exists and is valid (> 1KB), just clean up the orphan flac
    if [ -f "${target_mp3}" ] && [ "$(stat -c%s "${target_mp3}" 2>/dev/null || stat -f%z "${target_mp3}" 2>/dev/null)" -gt 1024 ]; then
      echo "[transcoder] Valid MP3 already exists for ${base}, removing leftover FLAC..."
      rm -f "${flac_path}"
      converted_folders+=("${dir}")
      continue
    fi

    # Transcode to .tmp
    if ffmpeg -nostdin -v error -y \
        -i "${flac_path}" \
        -c:a libmp3lame \
        -b:a "${BITRATE}" \
        -map_metadata 0 \
        -id3v2_version 3 \
        -f mp3 \
        "${tmp_mp3}"; then

      local size
      size="$(stat -c%s "${tmp_mp3}" 2>/dev/null || stat -f%z "${tmp_mp3}" 2>/dev/null || echo 0)"
      if [ "${size}" -gt 1024 ]; then
        mv -f "${tmp_mp3}" "${target_mp3}"
        rm -f "${flac_path}"
        echo "[transcoder] Converted successfully: ${target_mp3} (${size} bytes)"
        converted_folders+=("${dir}")
      else
        echo "[transcoder] ERROR: Output file ${tmp_mp3} was too small (${size} bytes), aborting removal"
        rm -f "${tmp_mp3}"
      fi
    else
      echo "[transcoder] ERROR: ffmpeg failed on ${flac_path}"
      rm -f "${tmp_mp3}"
    fi
  done < <(find "${MUSIC_DIR}" -type f -name "*.flac" -print0 2>/dev/null)

  # Notify Lidarr for distinct converted folders
  if [ ${#converted_folders[@]} -gt 0 ]; then
    local api_key=""
    if [ -f "${API_KEY_FILE}" ]; then
      api_key="$(cat "${API_KEY_FILE}")"
    elif [ -n "${LIDARR_API_KEY:-}" ]; then
      api_key="${LIDARR_API_KEY}"
    fi

    if [ -n "${api_key}" ]; then
      # Deduplicate folder list
      local unique_folders
      unique_folders=$(printf "%s\n" "${converted_folders[@]}" | sort -u)

      while IFS= read -r folder; do
        [ -z "${folder}" ] && continue
        echo "[transcoder] Notifying Lidarr to rescan folder: ${folder}"
        curl -s -X POST \
          -H "X-Api-Key: ${api_key}" \
          -H "Content-Type: application/json" \
          -d "{\"name\": \"RescanFolders\", \"folders\": [\"${folder}\"]}" \
          "${LIDARR_URL}/api/v1/command" >/dev/null 2>&1 || true
      done <<< "${unique_folders}"
    fi
  fi
}

# Main execution loop
if [ "${ONCE:-0}" = "1" ]; then
  run_pass
  exit 0
fi

while true; do
  run_pass
  sleep "${SCAN_INTERVAL}"
done
