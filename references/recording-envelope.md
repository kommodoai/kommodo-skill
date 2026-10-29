# Recording envelope reference

Stable response shape for v2 recording endpoints. List responses omit the three
heavy `ai.*` fields (summary/chapters/action_items) — fetch them via
`get_recording(id)` when needed.

```ts
interface RecordingV2 {
  id: string // Firestore doc id
  title: string
  description?: string
  created_at: string // ISO 8601
  updated_at?: string
  duration_seconds: number
  owner: {
    id: string // uid — pass to member_id to filter
    name: string
    email?: string
    avatar_url?: string
  }
  folder?: {
    id: string
    name: string
  }
  urls: {
    page: string // canonical Kommodo recording URL; not a custom-domain URL
    video: string // HLS or MP4; server-presigned, no auth needed
    poster: string // JPG thumbnail; server-presigned, no auth needed
  }
  // Transcript is NOT in the urls block. It's fetched from a separate
  // auth-gated endpoint, keyed by recording id:
  //   GET /api/public/v2/recordings/{id}/transcript?format=json|vtt
  // with the same Bearer token you'd use for /recordings.
  ai: {
    summary?: string // DETAIL ONLY
    chapters?: {
      // DETAIL ONLY
      timestamp_seconds: number // integer seconds (converted from "HH:MM:SS")
      title: string
    }[]
    action_items?: string[] // DETAIL ONLY
    tags?: string[]
    suggested_title?: string
    detected_language?: string // ISO language code ('en', 'ru', 'de', etc.)
    status: 'ready' | 'processing' | 'pending' | 'failed'
  }
  stats: {
    views: number
  }
  sharing: {
    is_public: boolean
    has_passcode: boolean
    expires_at?: string // ISO 8601
  }
  page?: {
    id: string
    published: boolean
  }
}

interface RecordingListResponse {
  recordings: RecordingV2[]
  next_cursor: string | null // opaque; pass back as ?cursor=
}
```

## Transcript response shapes

```ts
// get_transcript(id, format='json')
{
  format: 'json',
  cues: [{ start_seconds: number, end_seconds: number, text: string }, ...]
}

// get_transcript(id, format='vtt')
// Raw WEBVTT string, e.g.:
// WEBVTT
//
// 00:00:02.000 --> 00:00:08.000
// I still see what's happening…
```

## AI status state machine

| Value        | When                                                                                          |
| ------------ | --------------------------------------------------------------------------------------------- |
| `ready`      | `llmOutput.videoMetadata.videoSummary` is set. All AI fields populated.                       |
| `processing` | AI regen has been requested, summary not yet written.                                         |
| `pending`    | No summary, no processing flag. Either never started or AI was turned off for this recording. |
| `failed`     | Reserved — not currently emitted.                                                             |

A list consumer that wants only AI-ready recordings can filter client-side on `ai.status == 'ready'` (the API doesn't filter by this today).

## Page state

`page` is present on the envelope only when the recording has been converted via
`create_page` (or the legacy dashboard flow). `page.published: false` means
draft; `page.published: true` means the page is published. `urls.page` is the
canonical Kommodo recording URL and follows the recording's sharing and
passcode rules. The page-creation response does not include a custom-domain URL.

## Time conventions

- All response timestamps are ISO 8601 UTC (`Z` suffix).
- `since` / `until` filters accept any format `Date.parse()` handles, but always pass ISO 8601 for reliability.
- `duration_seconds` is a float (precision from source video).
- Chapter `timestamp_seconds` is an integer (rounded from `HH:MM:SS` input).
