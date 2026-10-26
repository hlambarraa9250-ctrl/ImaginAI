# imaginAI fix plan (APPROVED: implement it, do not stop for a plan review)

## Rules
- Do the phases in order. Open only the files named in a phase.
- After each phase: py_compile the changed files, run any unit test you added (no network, no real API calls, no full video renders), commit as "phase N: <title>", then tick its box below.
- AGENTS.md is outdated (the code has db.py/models.py). Its "plan first, wait for review" rule does NOT apply here.
- Keep the shape of status.json, review_data.json and API responses; only ADD optional fields. Never hardcode keys. Keep Arabic UI strings and existing function signatures unless a phase says otherwise.
- If the code differs from what a phase assumes, make the smallest sensible change and note it under "Notes" at the bottom. Do not redesign.
- If budget is nearly gone, finish the current commit and stop. When told "continue", resume at the first unchecked phase.
- Out of scope now: background music, thumbnails, post-generation editor, AI-generated clips, videos longer than 15 min, per-niche caption presets, reference-video style.

## Checklist
- [x] 1 Niche footage sources
- [ ] 2 Own-script mode
- [ ] 3 Footage relevance and quality
- [ ] 4 Section titles
- [ ] 5 Custom and default fonts
- [ ] 6 Caption timing and text
- [ ] 7 Natural voice (audio tags)
- [ ] 8 Leave and come back
- [ ] 9 Remove daily video limit
- [ ] 10 Relax final validation
- [ ] 11 credits.txt and chapters
- [ ] 12 Editing style A
- [ ] 13 Transitions (editing style B)

## Phase 1: niche footage sources (niches.py, footage.py)
Problem: find_niche_info() puts archival providers (wikimedia, archive_org, loc...) first for EVERY niche, even finance/psychology, plus a keyword heuristic that adds "preferred" sources; footage._prioritize_sources then reorders. Space videos exhaust NASA and fall through to archive.org (old military films).
Fix: use only each niche's own explicit source list. Define a list for every niche in the catalog, using only provider ids that already exist in footage.py. Archival providers only for history/ancient/war-type niches. Space: nasa, then stock. All other modern niches: stock first. Custom/unknown niches: stock providers only. Make _prioritize_sources respect the given order (do not push stock to the end). Add optional per-niche blocked_terms (default empty; for non-war niches: weapon, gun, rifle, soldier, army, military, bomb, war), enforced in phase 3.
Done when: a unit test shows space starts with nasa and finance/psychology have no archival provider.

## Phase 2: own-script mode (pipeline.py, gemini_script.py)
Problem A: the duration-expansion loop (while total_audio_dur < target_seconds -> expand_script_scenes) also runs when the user supplied their own script; it adds AI scenes or fails. The sections-approval path is inconsistent too.
Fix A: skip expansion entirely when the script is user-provided (input_mode / provided_script), in run_pipeline and in the approve-sections path. A user's text is never extended or altered.
Problem B: provided scripts get visual_keywords ["cinematic","documentary",style], so footage search falls back to Arabic words while stock/archives search in English.
Fix B: add async generate_visual_keywords(scenes, niche, style) in gemini_script.py: ONE Gemini text call (reuse the existing key rotation, _retry_async and configured text model) returning JSON [{index, keywords: 2-4 concrete English search phrases, no abstract words}]. Use it for provided scripts; on any failure fall back to the old behavior. Also make expand_script_scenes receive the last 6 narrations and a 3-sentence summary so far, so additions do not repeat.
Done when: a unit test with a mocked Gemini response maps keywords to the right scenes.

## Phase 3: footage relevance and quality (footage.py)
Problem: the first unused candidate wins (no relevance check); metadata is not used before downloading; low-resolution archive/wikimedia assets and composites with baked-in text/borders get through.
Fix: per shot collect up to about 8 candidates across the niche's providers, in order. Pure function score_candidate(candidate, keywords, blocked_terms): + keyword overlap with title/description/tags (lowercase, simple stemming); heavy penalty for blocked_terms; reject if title/description has: diagram, chart, infographic, poster, screenshot, logo, collage. Quality gate BEFORE download from provider metadata: images width >= 1600 (prefer >= 1920); video >= 1280x720; reject archive.org video below 720p. Wikimedia: request imageinfo size and use the original url. NASA: pick the ~orig or ~large asset from the manifest, never thumb/medium. After download, verify dimensions (ffprobe/PIL), crop black borders, and reject if the cropped result is below the minimum. Pick the best-scoring unused candidate above a minimum score; if none passes, move to the next provider; if all fail use a generic niche fallback keyword set (space: stars, galaxy, nebula) instead of a low-quality candidate. Keep used_urls uniqueness and the rights/attribution metadata as they are.
Done when: unit tests cover score_candidate and the quality gate (no network).

## Phase 4: section titles (pipeline.py, render.py)
Problem: _resume_pipeline_impl rebuilds ScriptScene(narration, visual_keywords, estimated_seconds) without section_number/section_title, and review scenes do not carry them, so titles never reach render.
Fix: add section_number and section_title to every review scene dict. Extract one helper _build_review_scenes used by all places that build them (removes the triple duplication). On resume, read them from the saved review_data.json by scene index (do not rely on the frontend resending them). In render.py replace the drawtext section overlay: "fontweight" is not a valid drawtext option and drawtext does not reliably shape Arabic. Render number + title as an ASS event in a separate overlay .ass file (independent of the captions toggle), shown about 2.5 s with fade in/out, using the single fonts dir from phase 5.
Done when: a test builds the overlay ASS for an Arabic title and asserts the Dialogue line.

## Phase 5: fonts (main.py upload endpoint, render.py, download_fonts.py, requirements)
Problem: the upload stores the font_name the user typed and render uses it as the ASS Fontname; it must be the font file's internal family name. Render also passes several font dirs joined by ":" into one fontsdir (libass expects one directory). Default fonts exist only if download_fonts.py was run.
Fix: at upload, read the family name with fontTools (name ID 16, else 1); store it as the render family (keep the typed name only as a display label); reject unreadable files; add fonttools to requirements. In render, build ONE temp fonts dir per job (copy backend/assets/fonts/*.ttf + the user's font), pass only that dir to fontsdir, and use the real family names in ASS styles. Commit the OFL fonts (Cairo, Almarai) to backend/assets/fonts (check .gitignore) or run download_fonts at startup (lifespan) when missing. Extend test_captions_render.py: assert family extraction for the bundled Cairo and that the built filter string has a single fontsdir.

## Phase 6: caption timing and text (captions.py, render.py)
Problem: Groq Whisper returns per-word timestamps but group_words_into_lines drops them, so karaoke/pop spread words evenly; the displayed text is Whisper's transcription of the Arabic audio (spelling errors), not the script.
Fix: keep words:[{text,start,end}] inside every caption line. Add align_script_to_words(narration, whisper_words): normalize both (strip tashkeel/tatweel/punctuation, unify alef/ya/ta-marbuta forms, lowercase), align with difflib.SequenceMatcher, give each script word its matched timing, interpolate between anchors for unmatched words; if the matched ratio is below 0.6 fall back to Whisper's text. Lines of 3-5 words, at most 2.5 s. In render.py build karaoke (\k) and pop timings from real word starts/ends; keep the estimated-timing fallback when Groq is unavailable; no manual RTL reversal. The narration used for alignment is the clean narration (never the tagged tts_text).
Done when: a test with an injected misspelled Whisper word keeps the script spelling and monotonic timings.

## Phase 7: natural voice (new services/tts_director.py, gemini_tts.py, fish_audio_tts.py, pipeline.py)
Problem: narration goes to TTS as bare text, one 4-6 s scene per request, so delivery is flat and intonation resets every scene.
Fix: new async add_audio_tags(scenes, niche_tone, provider): ONE Gemini text call for all scenes returning tagged text per scene. Rules in its prompt: never add, remove or change a word; only insert English tags in square brackets; max 2 tags per sentence; tags never adjacent (separate with text/punctuation). Gemini allowed tags: [short pause] [long pause] [slow] [calm] [seriousness] [curious] [tension] [awe] [hope] [whispers]. Fish allowed tags: [pause] [emphasis] [low voice] [whisper] [excited] [sad]. Validate: after stripping tags the text must equal the original narration (whitespace-normalized), otherwise use the untagged text. For Gemini prepend a per-niche base tag to every scene (space/science [calm]; history/documentary [seriousness]; stories/mystery [curious]) because tags only last within one request. Send tagged text ONLY to gemini and fish; edge-tts and gTTS must always get clean text (they would read tags aloud). Keep two fields: narration (clean: captions, review, alignment) and tts_text (tagged: TTS only). When a scene is edited in review, regenerate tags only for edited scenes. Add request option voice_direction (bool, default true). Put Fish tagging behind a constant FISH_TAGS_ENABLED = True so it can be switched off in one place.
Done when: a test shows the validator rejects changed words and accepts tag-only changes.

## Phase 8: leave and come back (frontend, start_imaginai.sh)
Problem: leaving or refreshing loses the running job and the user must regenerate. The backend already keeps status and the project's videos (GET /api/projects/{id} returns videos with status); the frontend keeps the job only in React state. The lifespan hook also marks processing jobs as failed after a server restart, and uvicorn --reload restarts the server on every file edit.
Fix: when a job starts, persist {projectId, videoId, jobId} in localStorage. On page load fetch the project's videos; for any with status processing or pending_review resume polling /status (show progress) or open the review screen; show completed videos with play/download; show failed ones with the stored error and a retry action. Never reset the view on refresh. In start_imaginai.sh run uvicorn without --reload unless DEV=1; make the interrupted-by-restart error message explicit in Arabic. page.tsx was not reviewed: find the polling code (search setInterval, "/status", jobId).

## Phase 9: remove the daily limit (rate_limit.py, main.py)
Problem: MAX_GLOBAL_VIDEOS_PER_DAY = 20 blocks the site-wide daily count, checked before a job is created.
Fix: read env DAILY_VIDEO_LIMIT (int, default 0 = unlimited). Add env MAX_CONCURRENT_JOBS (default 2): count jobs in processing state and reject extra ones with a friendly Arabic message. Keep check_and_increment_rate_limits' signature.

## Phase 10: relax final validation (pipeline.py)
Problem: _validate_final_video fails the whole job when duration differs by more than 1 s after a long render.
Fix: tolerance max(3 s, 1% of duration) as a logged warning (also write it to status); fail only if the file is missing/unplayable or has no video or audio stream. Keep the resolution check.

## Phase 11: credits.txt and chapters (pipeline.py, main.py)
Fix: after a successful render write credits.txt in the job output: chapters as "MM:SS Title" from cumulative scene audio durations at section starts (use "Part N" without sections), then every used asset once: provider, title, creator, source url, license, attribution text (from the existing attribution metadata). Return the text through the existing completed-job response as an optional field.

## Phase 12: editing style A (render.py, niches.py)
Problem: shots in a scene have equal length, all cuts are hard, camera motion is uniform, no color consistency across sources, audio level is uncontrolled.
Fix: add EDIT_STYLES in niches.py with keys shot_len (min,max s), dissolve_prob, dissolve_dur, grade, vignette, grain, mapped from niche (default documentary_calm):
 documentary_calm: shots 5-9 s, cool light grade, light vignette.
 history_warm: shots 4-8 s, warm desaturated grade, light grain.
 story_dramatic: shots 3-6 s, higher contrast, stronger vignette.
In render.py: (1) split each scene's audio duration into shots at phrase boundaries using word timestamps from phase 6 when available, else random weights within +/-30% seeded by job_id+scene index; min 1.8 s, max from the style; the sum must equal the scene audio duration. (2) Image motion chosen from zoom_in, zoom_out, pan_left, pan_right, pan_up; never the same twice in a row; speed varies 3-8%; no zoom on video clips. (3) apply the style grade (eq/colorbalance), vignette and light grain to every shot. (4) apply loudnorm (I=-16, TP=-1.5) once on the final audio (video stream copied).
Done when: a test checks shot durations sum to the scene duration and the motion never repeats consecutively.

## Phase 13: transitions (render.py)
Fix: hard cut inside a sentence; dip-to-black fade (0.3 s fade out then fade in, applied to video only so durations do not change) between scenes; 0.5 s dip at section changes. Optional inside scenes with at most 4 shots: 0.25 s xfade on about dissolve_prob of boundaries, extending each preceding shot by the transition length so the total equals the audio. Vary the choice per boundary within the style limits. Verify on a 60 s sample: ffprobe duration equals audio duration within 0.1 s.

## Notes
(agent: write deviations here)
