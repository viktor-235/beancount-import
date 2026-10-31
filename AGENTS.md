# Repository rules

- Do not add `Co-Authored-By` lines or any other agent attribution to commit messages.
- When a change alters the UI, retake the affected screenshots in `docs/images` (always the main `review.png`) before committing. Build the frontend, run the demo (`python examples/jev/run.py --mock-jev`) and keep the same size and scale as the existing image (`review.png`: 1400×610 viewport at 2x, after clicking Classify with AI).
