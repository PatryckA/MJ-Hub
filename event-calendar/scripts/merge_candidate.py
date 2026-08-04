name: Handle candidate event PR decision

on:
  pull_request:
    types: [closed]
    paths:
      - "event-calendar/data/candidates/**"

permissions:
  contents: write
  issues: write

jobs:
  handle-decision:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: main
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Configure git identity
        run: |
          git config user.name "on-the-floor-scraper"
          git config user.email "actions@users.noreply.github.com"

      - name: Get changed candidate file path
        id: files
        run: |
          echo "path=$(git diff --name-only ${{ github.event.pull_request.base.sha }} ${{ github.event.pull_request.head.sha }} -- event-calendar/data/candidates/ | head -n1)" >> "$GITHUB_OUTPUT"

      # --- Approved (merged) path ---
      - name: Merge candidate into events.json
        if: github.event.pull_request.merged == true
        run: python event-calendar/scripts/merge_candidate.py "${{ steps.files.outputs.path }}"
        id: merge_step
        continue-on-error: true

      - name: Commit merged event
        if: github.event.pull_request.merged == true && steps.merge_step.outcome == 'success'
        run: |
          git add event-calendar/data/events.json "${{ steps.files.outputs.path }}" 2>/dev/null || true
          git commit -m "Merge candidate event into events.json"
          git pull --rebase origin main
          git push origin HEAD:main

      - name: Open ID-collision issue
        if: github.event.pull_request.merged == true && steps.merge_step.outcome == 'failure'
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          gh issue create \
            --title "ID collision merging ${{ steps.files.outputs.path }}" \
            --body "The scraper's merge step hit an id collision for this candidate. It was NOT auto-merged into events.json. Please check ${{ steps.files.outputs.path }} manually and resolve the id clash before adding it by hand."

      # --- Denied (closed without merging) path ---
      - name: Fetch candidate content from head branch
        if: github.event.pull_request.merged != true
        run: |
          git fetch origin "${{ github.event.pull_request.head.ref }}" || true
          git show "origin/${{ github.event.pull_request.head.ref }}:${{ steps.files.outputs.path }}" > /tmp/rejected_candidate.json || echo "{}" > /tmp/rejected_candidate.json

      - name: Record rejection
        if: github.event.pull_request.merged != true
        run: python event-calendar/scripts/reject_candidate.py /tmp/rejected_candidate.json || true

      - name: Commit rejection record
        if: github.event.pull_request.merged != true
        run: |
          git add event-calendar/data/rejected.json
          git commit -m "Record denied candidate event"
          git pull --rebase origin main
          git push origin HEAD:main

      - name: Delete candidate branch
        if: always()
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: gh api -X DELETE "repos/${{ github.repository }}/git/refs/heads/${{ github.event.pull_request.head.ref }}" || true
