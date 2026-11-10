#!/usr/bin/env bash
# ==============================================================================
# Anna's Archive Metadata & Manifests Ingestion Runner
# ==============================================================================

set -euo pipefail

BASE_DIR="/mnt/storage/annas_archive"
MANIFESTS_DIR="${BASE_DIR}/manifests"
METADATA_DIR="${BASE_DIR}/metadata"
TORRENTS_DIR="${BASE_DIR}/torrents"
LOG_FILE="${BASE_DIR}/ingestion.log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST_LIST="${SCRIPT_DIR}/manifest_urls_aria2.txt"
DERIVED_TORRENT="${SCRIPT_DIR}/aa_derived_mirror_metadata_20260208.torrent"

mkdir -p "${MANIFESTS_DIR}" "${METADATA_DIR}" "${TORRENTS_DIR}"

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "${msg}" | tee -a "${LOG_FILE}"
}

log "======================================================================"
log "Anna's Archive Ingestion Pipeline Started"
log "Target Directory: ${BASE_DIR}"
log "======================================================================"

# ------------------------------------------------------------------------------
# Phase 1: Download all 22,640 .torrent data container manifests (~350 MB)
# ------------------------------------------------------------------------------
log "[Phase 1/2] Downloading 22,640 BitTorrent data container manifests..."

if [[ -f "${MANIFEST_LIST}" ]]; then
    aria2c -i "${MANIFEST_LIST}" \
           --follow-torrent=false \
           --auto-file-renaming=false \
           --conditional-get=true \
           -j 16 -c \
           --max-connection-per-server=4 \
           --user-agent="Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0" \
           --summary-interval=10 \
           --log="${BASE_DIR}/manifests_download.log" \
           --log-level=notice || true
    
    TOTAL_MANIFESTS=$(find "${MANIFESTS_DIR}" -type f -name '*.torrent' | wc -l)
    log "[Phase 1/2] Container manifests download completed. Total files: ${TOTAL_MANIFESTS}"
else
    log "[!] Manifest list ${MANIFEST_LIST} not found!"
fi

# ------------------------------------------------------------------------------
# Phase 2: Download Core Relational Metadata Tables (~67.5 GB)
# ------------------------------------------------------------------------------
log "[Phase 2/2] Downloading Core Relational Metadata Tables via BitTorrent Swarm..."

if [[ -f "${DERIVED_TORRENT}" ]]; then
    # Selected files:
    # 806-891: computed_all_md5s, isbndb_isbns, libgenli_*, libgenrs_*
    # 904-912: torrents_json, zlib_book, zlib_isbn
    # 188-189: aarecords_all_md5
    aria2c --dir="${METADATA_DIR}" \
           --seed-time=0 \
           --max-connection-per-server=16 \
           --split=16 \
           --min-split-size=10M \
           --select-file=188-189,806-891,904-912 \
           --summary-interval=30 \
           --log="${BASE_DIR}/metadata_swarm.log" \
           --log-level=warn \
           "${DERIVED_TORRENT}" || true
    log "[Phase 2/2] Core metadata download completed."
else
    log "[!] Torrent file ${DERIVED_TORRENT} not found!"
fi

log "======================================================================"
log "Anna's Archive Ingestion Pipeline Finished"
log "======================================================================"
