name: Uppdatera nyckeltal

on:
  schedule:
    - cron: "30 22 * * 1-5"
  workflow_dispatch: {}

permissions:
  contents: write
  issues: write

concurrency:
  group: update-data
  cancel-in-progress: false

jobs:
  update:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    defaults:
      run:
        working-directory: full-throttle-repo/full-throttle-site
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Installera
        run: pip install -r requirements.txt
      - name: Hämta nyckeltal och 200W
        run: python scripts/update.py
      - name: Mejllarm vid nya signaler
        if: hashFiles('full-throttle-repo/full-throttle-site/alerts.md') != ''
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          gh issue create --title "Nya signaler $(date -u +%Y-%m-%d)" --body-file alerts.md
          rm alerts.md
      - name: Spara ny data
        run: |
          rm -f alerts.md
          git add -N history.json 2>/dev/null || true
          if git diff --quiet -- data.json history.json; then echo "Ingen ändring."; exit 0; fi
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data.json history.json
          git commit -m "Nyckeltal $(date -u +%Y-%m-%d)"
          git push
