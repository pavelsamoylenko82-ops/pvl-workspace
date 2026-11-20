#!/bin/bash
set -e
echo "--- BOM check /etc/nginx/conf.d ---"
found=0
while IFS= read -r -d '' f; do
  if [ "$(head -c 3 "$f" | od -An -tx1 | tr -d ' \n')" = "efbbbf" ]; then
    echo "BOM: $f"
    found=1
  fi
done < <(find /etc/nginx/conf.d -maxdepth 1 -type f -print0)
if [ "$found" -eq 0 ]; then echo "No BOM files"; fi
echo "--- nginx -t ---"
nginx -t
echo "--- reload ---"
systemctl reload nginx
systemctl is-active nginx