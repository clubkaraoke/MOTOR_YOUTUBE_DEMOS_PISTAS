# DJGABO CDG / MP4 ENGINE V2 — Architecture Contract

Status: **CDG V2 validation in progress. MP4 V2 is intentionally NOT implemented yet.**

## Safety contract

- Production `/cdg/`, `/opt/djgabo-cdg`, service `djgabo-cdg` and its renderer stay untouched.
- All V2 work lives in the isolated clone under `/cdg-v2/`.
- V2 test renders never publish to Dropbox.
- Existing panel workflow remains: Home / Jobs / QR or upload / ElevenLabs / editor / save project.
- Only the right-side render studio and V2 render routes are new.

## Canonical architecture

```
                         ElevenLabs
                        START / END
                             |
                             v
                      timeline_v2.json
                             |
              +--------------+--------------+
              |              |              |
              v              v              v
         PREVIEW V2      CDG ENGINE V2   MP4 ENGINE V2
                              |              |
                              v              v
                       output_v2.cdg        ASS V2
                                              |
                                              v
                                            FFmpeg
                                              |
                                              v
                                       output_v2.mp4
                                          1280x720
```

## Fundamental time rule

`timeline_v2.json` is the single musical clock.

Every canonical word has:
- `text`
- `start`
- `end`

Those values come from the edited ElevenLabs-aligned project and are immutable.

A renderer may choose presentation details, but may never move a word in musical time.

CDG may decide:
- CDG tiles
- CDG page/slot layout
- line preparation / erase scheduling
- wrapping needed by the 300x216 CDG surface

Future MP4 may decide:
- 1280x720 layout
- typeface
- font size
- outline
- shadow
- screen position
- ASS event formatting

Neither may change canonical START/END.

## timeline_v2.json is NOT a CDG JSON

The master document is divided conceptually into:

```
timeline_v2.json
|
+-- audio
|   +-- duration
|
+-- words
|   +-- id
|   +-- text
|   +-- start
|   +-- end
|   +-- role
|
+-- segments
|
+-- render_metadata
|
+-- layouts
    +-- cdg
    |   +-- layout
    |   +-- lines/pages/slots
    |
    +-- mp4   <-- null until the MP4 phase is approved
```

During CDG validation the old top-level `layout` and `lines` keys remain as compatibility aliases only. They are not a second timing source.

## CDG V2 validation gate — STOP HERE until approved

Reference job: **LET-0088 · Hermanos Silva — El Perfume**

Must prove:

```
ElevenLabs START/END
        =
timeline_v2.json
        =
Preview V2
        =
decoded real CDG V2
```

Required checks:
1. isolated `/cdg-v2/` deployment;
2. smoke test;
3. real render of El Perfume;
4. compare concrete words at beginning / middle / end;
5. no accumulated offset;
6. `intro_delay = 0`;
7. wrapping/pages do not modify canonical START/END;
8. production code hash and `/cdg/` remain unchanged.

After these checks, **stop and report results before implementing MP4**.

## Future MP4 V2 — research only until CDG gate passes

Nomad files already selected for study:

- `karaoke_gen/lyrics_transcriber/output/generator.py`
- `karaoke_gen/lyrics_transcriber/output/subtitles.py`
- `karaoke_gen/lyrics_transcriber/output/video.py`
- `backend/services/local_encoding_service.py`

Secondary reference:
- `karaoke_gen/lyrics_transcriber/output/cdgmaker/composer.py::create_mp4()`

Research conclusion:
- Nomad models CDG and video as separate output paths.
- Its normal video path builds ASS-style karaoke subtitles and renders/encodes with FFmpeg.
- The CDG composer's `create_mp4()` also creates ASS for high-quality lyrics instead of treating decoded CDG lyrics as the authoritative video presentation.
- Therefore DJGABO MP4 V2 must read `timeline_v2.json` directly, generate its own ASS, and render H.264 independently.
- MP4 V2 must never read `output_v2.cdg`.
- A CDG failure must not affect MP4 V2, and an MP4 style change must not affect CDG V2.

Planned future path after approval:

```
timeline_v2.json
       |
       v
     ASS V2
       |
       v
     FFmpeg
       |
       v
 H.264 + instrumental
       |
       v
 output_v2.mp4 1280x720
```

No MP4 implementation is permitted before the El Perfume equality report.
