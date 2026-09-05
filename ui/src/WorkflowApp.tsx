import { useEffect, useMemo, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import './WorkflowApp.css'

type Phase = 'metadata' | 'materials' | 'analysis' | 'review' | 'render'
type Job = { job_id: string; status: string; progress: number; transcription_progress?: number; phase?: string; message: string; error?: string; result?: { revision?: number } }
type LanguageOption = { value: string; label: string }
type Metadata = { title?: string; video_id?: string; channel?: string; upload_date?: string; duration_seconds?: number; view_count?: number; like_count?: number; comment_count?: number; description?: string; categories?: string[]; tags?: string[]; thumbnail?: string; thumbnail_files?: { url: string; source_url?: string; is_primary?: boolean }[]; chapters?: { start_time: number; end_time: number; title?: string }[]; heatmap?: { start_time: number; end_time: number; value: number }[]; subtitles_available?: boolean; captions_available?: boolean; subtitle_languages?: LanguageOption[]; caption_languages?: LanguageOption[]; chat_replay_available?: boolean }
type Section = { section_id: string; start: number; end: number; segment_ids: string[]; text: string; llm_score?: number; selected: boolean }
type Chapter = { chapter_id: string; summary: string; llm_score: number; start: number; end: number; sections: Section[] }
type Segment = Section & { segment_id: string; chapter_id: string }
type Review = { target_seconds: number; recommended_segment_ids: string[]; chapters: Chapter[] }
type MaterialKind = 'comments' | 'chat' | 'subtitles' | 'captions'
type MaterialArtifact = { kind: MaterialKind; label: string; path: string; format: string; count: number | null; total_count?: number | null; preview: unknown }
type MaterialDownloadJob = { job_id: string; status: 'running' | 'completed' | 'failed'; progress: number; message: string; error?: string; result?: { artifacts?: MaterialArtifact[] } }

const initialSettings = { llm_provider: 'deepseek' as 'gemini' | 'deepseek', genre: 'ai_news' as 'ai_news' | 'stock' | 'game', target_duration_seconds: 600, transcription_source: 'youtube_caption' as 'youtube_caption' | 'youtube_subtitle' | 'whisper_api', stt_language: 'ko', stt_initial_prompt: '', stt_hotwords: '', stt_speed: 1, subtitle_font_name: 'Malgun Gothic', subtitle_font_size: 18, render_mode: 'preview' as 'preview' | 'exact' }
const formatTime = (value?: number) => { const seconds = Math.max(0, Math.round(value || 0)); return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60].map(part => String(part).padStart(2, '0')).join(':') }
const formatMilliseconds = (value?: number) => { const milliseconds = Math.max(0, Math.round((value || 0) * 1000)); const seconds = Math.floor(milliseconds / 1000); return `${[Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60].map(part => String(part).padStart(2, '0')).join(':')}.${String(milliseconds % 1000).padStart(3, '0')}` }
function DetailedTime({ value }: { value?: number }) { const formatted = formatMilliseconds(value); return <span className="detailed-time"><span>{formatted.slice(0, -4)}</span><small>{formatted.slice(-4)}</small></span> }
const formatDate = (value?: string) => { const match = value?.match(/^(\d{4})[-./]?(\d{2})[-./]?(\d{2})/); return match ? `${match[1]}-${match[2]}-${match[3]}` : value || '확인 불가' }
const isYouTubeVideoUrl = (value: string) => { try { const parsed = new URL(value); const host = parsed.hostname.toLowerCase().replace(/^www\./, ''); const id = host === 'youtu.be' ? parsed.pathname.slice(1).split('/')[0] : parsed.searchParams.get('v') || parsed.pathname.match(/^\/(?:shorts|live|embed)\/([^/?]+)/)?.[1]; return (host === 'youtu.be' || host === 'youtube.com' || host.endsWith('.youtube.com')) && Boolean(id && /^[\w-]{11}$/.test(id)) } catch { return false } }

export default function WorkflowApp() {
  const [client, setClient] = useState<SupabaseClient | null>(null)
  const [token, setToken] = useState('')
  const [accountEmail, setAccountEmail] = useState('')
  const [phase, setPhase] = useState<Phase>('metadata')
  const [theme, setTheme] = useState<'light' | 'dark'>(() => window.localStorage.getItem('ave-theme') === 'dark' ? 'dark' : 'light')
  const [url, setUrl] = useState('')
  const [metadata, setMetadata] = useState<Metadata | null>(null)
  const [metadataMotion, setMetadataMotion] = useState<'initial' | 'loading' | 'loaded'>('initial')
  const [metadataTab, setMetadataTab] = useState<'overview' | 'description' | 'chapters' | 'heatmap'>('overview')
  const [materialSelections, setMaterialSelections] = useState<Record<MaterialKind, boolean>>({ comments: false, chat: false, subtitles: false, captions: false })
  const [subtitleLanguage, setSubtitleLanguage] = useState('')
  const [captionLanguage, setCaptionLanguage] = useState('')
  const [materials, setMaterials] = useState<MaterialArtifact[]>([])
  const [materialTab, setMaterialTab] = useState<MaterialKind | null>(null)
  const [materialDownload, setMaterialDownload] = useState<MaterialDownloadJob | null>(null)
  const [settings, setSettings] = useState(initialSettings)
  const [message, setMessage] = useState('로그인 설정을 불러오는 중입니다.')
  const [busy, setBusy] = useState(false)
  const [job, setJob] = useState<Job | null>(null)
  const [review, setReview] = useState<Review | null>(null)
  const [openChapters, setOpenChapters] = useState<Set<string>>(new Set())
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const [now, setNow] = useState(Date.now())
  const [previewEnd, setPreviewEnd] = useState<number | null>(null)
  const sourcePreviewRef = useRef<HTMLVideoElement>(null)
  const shouldAnimateMetadataRef = useRef(true)
  const displayedMetadataRef = useRef<Metadata | null>(null)
  const displayedMetadataUrlRef = useRef('')
  const previousPhaseRef = useRef<Phase>(phase)
  const restoredJobRef = useRef(false)
  const phaseTransitionRef = useRef(false)
  const scriptSourceOptions = useMemo(() => [
    ...(metadata?.subtitles_available ? [{ value: 'youtube_subtitle' as const, label: '자막' }] : []),
    ...(metadata?.captions_available ? [{ value: 'youtube_caption' as const, label: '캡션' }] : []),
    { value: 'whisper_api' as const, label: 'Whisper' },
  ], [metadata])

  function transitionToPhase(next: Phase) {
    if (next === phase || phaseTransitionRef.current) return
    const panel = document.querySelector<HTMLElement>('.workflow-shell .panel')
    if (!panel) { setPhase(next); return }
    phaseTransitionRef.current = true
    panel.style.overflow = 'hidden'
    panel.style.height = `${panel.getBoundingClientRect().height}px`
    panel.style.transition = 'none'
    window.requestAnimationFrame(() => {
      panel.style.transition = 'height 360ms cubic-bezier(.4, 0, 1, 1), opacity 240ms ease, transform 360ms cubic-bezier(.4, 0, 1, 1)'
      panel.style.height = '0px'
      panel.style.opacity = '0'
      panel.style.transform = 'translateY(-16px) scaleY(.98)'
      window.setTimeout(() => {
        flushSync(() => setPhase(next))
        const nextPanel = document.querySelector<HTMLElement>('.workflow-shell .panel')
        if (!nextPanel) { phaseTransitionRef.current = false; return }
        nextPanel.style.overflow = 'hidden'
        const height = nextPanel.scrollHeight
        nextPanel.style.height = '0px'
        nextPanel.style.opacity = '0'
        nextPanel.style.transform = 'translateY(-16px) scaleY(.98)'
        nextPanel.style.transition = 'none'
        void nextPanel.offsetHeight
        window.requestAnimationFrame(() => {
          nextPanel.style.transition = 'height 520ms cubic-bezier(.22, 1, .36, 1), opacity 320ms ease, transform 520ms cubic-bezier(.22, 1, .36, 1)'
          nextPanel.style.height = `${height}px`
          nextPanel.style.opacity = '1'
          nextPanel.style.transform = 'translateY(0) scaleY(1)'
          window.setTimeout(() => { nextPanel.style.height = ''; nextPanel.style.opacity = ''; nextPanel.style.transform = ''; nextPanel.style.transition = ''; nextPanel.style.overflow = ''; phaseTransitionRef.current = false }, 540)
        })
      }, 380)
    })
  }

  useEffect(() => { document.documentElement.dataset.theme = theme; window.localStorage.setItem('ave-theme', theme) }, [theme])
  useEffect(() => { document.documentElement.dataset.metadataState = metadataMotion }, [metadataMotion])
  useEffect(() => { if (phase === 'metadata' && previousPhaseRef.current !== 'metadata') setMetadataTab('overview'); previousPhaseRef.current = phase }, [phase])
  useEffect(() => { if (phase !== 'metadata') return; const input = document.querySelector<HTMLInputElement>('.url-control input[type="url"]'); if (!input) return; const selectAll = () => input.select(); input.addEventListener('focus', selectAll); return () => input.removeEventListener('focus', selectAll) }, [phase])
  useEffect(() => { if (metadata) { displayedMetadataRef.current = metadata; return }; if (!busy && displayedMetadataRef.current) setMetadata(displayedMetadataRef.current) }, [metadata, busy])
  useEffect(() => { if (busy && shouldAnimateMetadataRef.current) { setMetadataMotion('loading'); return }; if (!metadata) { setMetadataMotion('initial'); return }; if (!shouldAnimateMetadataRef.current) { setMetadataMotion('loaded'); return }; const timer = window.setTimeout(() => setMetadataMotion('loaded'), 800); return () => window.clearTimeout(timer) }, [busy, metadata])
  useEffect(() => { const onMetadataConfirm = (event: MouseEvent) => { const button = event.target instanceof Element ? event.target.closest('button') : null; if (metadata || button?.textContent?.trim() !== '정보 확인' || !isYouTubeVideoUrl(url.trim())) return; const shell = document.querySelector<HTMLElement>('.workflow-shell'); const panel = shell?.querySelector<HTMLElement>('.panel'); if (!shell || !panel) return; const initialTop = panel.getBoundingClientRect().top; shell.style.minHeight = '0'; const offset = initialTop - panel.getBoundingClientRect().top; const animation = panel.animate([{ transform: `translateY(${offset}px)` }, { transform: 'translateY(0)' }], { duration: 900, easing: 'cubic-bezier(.22, 1, .36, 1)', fill: 'both' }); window.setTimeout(() => { animation.cancel(); shell.style.minHeight = '' }, 920) }; document.addEventListener('click', onMetadataConfirm, true); return () => document.removeEventListener('click', onMetadataConfirm, true) }, [url, metadata])
  useEffect(() => { document.querySelectorAll('video').forEach(video => video.pause()); setPreviewEnd(null); if (restoredJobRef.current) { restoredJobRef.current = false; return }; window.scrollTo({ top: 0, behavior: 'smooth' }) }, [phase])
  useEffect(() => { if (!startedAt) return; const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer) }, [startedAt])
  useEffect(() => { let unsubscribe: (() => void) | undefined; fetch('/api/config').then(async response => { if (!response.ok) throw new Error('AVE 서버에서 로그인 설정을 불러오지 못했습니다.') ; return response.json() }).then(config => { const next = createClient(config.supabase_url, config.supabase_anon_key); setClient(next); const { data: { subscription } } = next.auth.onAuthStateChange((_event, session) => { setToken(session?.access_token || ''); setAccountEmail(session?.user.email || '') }); unsubscribe = () => subscription.unsubscribe(); return next.auth.getSession() }).then(({ data }) => { setToken(data.session?.access_token || ''); setAccountEmail(data.session?.user.email || ''); setMessage(data.session ? '로그인되었습니다. 영상 URL을 확인하세요.' : 'Google 로그인이 필요합니다.') }).catch(error => setMessage(error.message)); return () => unsubscribe?.() }, [])
  useEffect(() => { if (job) return; if (window.sessionStorage.getItem('ave-cancel-active-on-reload') === '1') { window.sessionStorage.removeItem('ave-cancel-active-on-reload'); return } fetch('/api/youtube/edit/active').then(response => response.ok ? response.json() : { jobs: [] }).then(value => { const active = Array.isArray(value.jobs) ? value.jobs[0] as Job | undefined : undefined; if (!active) return; restoredJobRef.current = true; setJob(active); setStartedAt(Date.now()); setPhase(active.status === 'awaiting_selection' || active.phase === 'selection' ? 'review' : active.phase === 'render' ? 'render' : 'analysis'); setMessage(active.message || '진행 중인 작업을 다시 연결했습니다.') }).catch(() => undefined) }, [job])
  useEffect(() => { if (phase === 'metadata') return; const requestCancellation = () => { window.sessionStorage.setItem('ave-cancel-active-on-reload', '1'); void fetch('/api/youtube/edit/cancel-active', { method: 'POST', keepalive: true }) }; const warnBeforeExit = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = true }; const cancelAfterExitConfirmed = (event: PageTransitionEvent) => { if (!event.persisted) requestCancellation() }; window.addEventListener('beforeunload', warnBeforeExit); window.addEventListener('pagehide', cancelAfterExitConfirmed); return () => { window.removeEventListener('beforeunload', warnBeforeExit); window.removeEventListener('pagehide', cancelAfterExitConfirmed) } }, [phase])
  useEffect(() => {
    if (!job || ['completed', 'failed', 'cancelled', 'awaiting_selection'].includes(job.status)) return
    const stream = new EventSource(`/api/youtube/edit/${job.job_id}/events`)
    const apply = (next: Job) => {
      setJob(current => current && JSON.stringify(current) === JSON.stringify(next) ? current : next)
      setMessage(next.error || next.message)
      if (['completed', 'failed', 'cancelled', 'awaiting_selection'].includes(next.status)) { setStartedAt(null); stream.close() }
    }
    stream.onmessage = event => { try { apply(JSON.parse(event.data) as Job) } catch { setMessage('작업 상태 SSE 메시지를 해석하지 못했습니다.') } }
    stream.onerror = () => {
      if (stream.readyState === EventSource.CLOSED) return
      // SSE 재연결 중에도 상태 API로 마지막 전환 이벤트를 보완한다.
      void fetch(`/api/youtube/edit/status/${job.job_id}`).then(async response => {
        if (response.ok) return response.json()
        if (response.status === 404) {
          setJob(current => current ? { ...current, status: 'failed', error: '작업이 종료되어 임시 파일이 정리되었습니다.' } : current)
          setStartedAt(null)
          setMessage('작업이 종료되어 임시 파일이 정리되었습니다. 직전 오류는 로컬 서버 로그에서 확인하세요.')
          stream.close()
        }
        return null
      }).then(value => { if (value) apply(value as Job) }).catch(() => undefined)
    }
    return () => stream.close()
  }, [job?.job_id, job?.status])
  useEffect(() => { if (job?.status !== 'awaiting_selection') return; fetch(`/api/youtube/edit/${job.job_id}/segments`).then(async response => { if (!response.ok) throw new Error((await response.json()).detail || '추천 구간을 불러오지 못했습니다.'); return response.json() }).then((value: Review) => { flushSync(() => { setReview(value); setOpenChapters(new Set()) }); transitionToPhase('review') }).catch(error => setMessage(error.message)) }, [job])
  useEffect(() => { if (phase !== 'review' || !review) return; const inputs = document.querySelectorAll<HTMLInputElement>('.chapter-head input[type="checkbox"]'); review.chapters.forEach((chapter, index) => { const selectedCount = chapter.sections.filter(section => section.selected).length; if (inputs[index]) inputs[index].indeterminate = selectedCount > 0 && selectedCount < chapter.sections.length }) }, [phase, review])
  useEffect(() => { if (phase !== 'review') return; document.querySelectorAll<HTMLButtonElement>('.chapter-head button.ghost.compact').forEach(button => { const opened = button.textContent?.trim() === '접기'; button.textContent = opened ? '▲' : '▼'; button.setAttribute('aria-label', opened ? '접기' : '세부 구간 보기'); button.title = opened ? '접기' : '세부 구간 보기' }) }, [phase, openChapters])
  useEffect(() => { if (job?.status === 'completed') transitionToPhase('render') }, [job])
  useEffect(() => { const first = scriptSourceOptions[0]?.value; if (first && !scriptSourceOptions.some(option => option.value === settings.transcription_source)) setSettings(current => ({ ...current, transcription_source: first })) }, [scriptSourceOptions, settings.transcription_source])
  useEffect(() => { if (phase !== 'metadata' || !metadata) return; document.querySelectorAll<HTMLButtonElement>('.metadata-card nav button').forEach(button => { const label = button.textContent?.trim(); const supported = label === '설명' ? Boolean(metadata.description?.trim()) : label === '챕터' ? Boolean(metadata.chapters?.length) : label === '히트맵' ? Boolean(metadata.heatmap?.length) : true; button.disabled = !supported; button.title = supported ? '' : '제공되지 않는 정보입니다.' }) }, [phase, metadata, metadataTab])
  useEffect(() => { if (phase !== 'metadata' || !metadata) return; const supported = metadataTab === 'description' ? Boolean(metadata.description?.trim()) : metadataTab === 'chapters' ? Boolean(metadata.chapters?.length) : metadataTab === 'heatmap' ? Boolean(metadata.heatmap?.length) : true; if (!supported) setMetadataTab('overview') }, [phase, metadata, metadataTab])
  useEffect(() => { if (!metadata || !shouldAnimateMetadataRef.current) return; const timer = window.setTimeout(() => { const card = document.querySelector<HTMLElement>('.metadata-card'); if (!card) return; card.style.maxHeight = 'none'; card.style.height = '0px'; card.style.overflow = 'hidden'; card.style.opacity = '0'; card.style.transform = 'translateY(-24px) scaleY(.96)'; card.style.transition = 'none'; setMetadataMotion('loaded'); window.requestAnimationFrame(() => { const height = card.scrollHeight; void card.offsetHeight; card.style.transition = 'height 900ms cubic-bezier(.22, 1, .36, 1), opacity 500ms ease 100ms, transform 900ms cubic-bezier(.22, 1, .36, 1)'; card.style.height = `${height}px`; card.style.opacity = '1'; card.style.transform = 'translateY(0) scaleY(1)'; window.setTimeout(() => { card.style.height = ''; card.style.maxHeight = ''; card.style.overflow = ''; card.style.opacity = ''; card.style.transform = ''; card.style.transition = '' }, 920) }) }, 800); return () => window.clearTimeout(timer) }, [metadata])

  const selected = useMemo(() => review?.chapters.flatMap(chapter => chapter.sections.filter(section => section.selected).map(section => ({ ...section, segment_id: section.section_id }))) || [], [review])
  const selectedDuration = useMemo(() => selected.reduce((total, segment) => total + Math.max(0, segment.end - segment.start), 0), [selected])
  const elapsed = startedAt ? Math.floor((now - startedAt) / 1000) : 0
  const controlsLocked = busy || Boolean(job && !['completed', 'failed', 'cancelled', 'awaiting_selection'].includes(job.status))
  const progressValue = materialDownload ? materialDownload.progress : job?.phase === 'transcription' ? job.transcription_progress || 0 : job?.progress || 0
  const progressLabel = materialDownload ? '추가 메타데이터 진행률' : job?.phase === 'transcription' ? 'Whisper 전사 진행률' : phase === 'render' ? '렌더링 진행률' : 'AI 편집 진행률'
  const setSetting = <K extends keyof typeof initialSettings>(key: K, value: (typeof initialSettings)[K]) => { if (!controlsLocked) setSettings(current => ({ ...current, [key]: value })) }
  const updateSelection = (ids: Set<string>) => setReview(current => current ? { ...current, chapters: current.chapters.map(chapter => ({ ...chapter, sections: chapter.sections.map(section => ({ ...section, selected: ids.has(section.section_id) })) })) } : current)

  async function login() { if (!client) return; const { error } = await client.auth.signInWithOAuth({ provider: 'google', options: { redirectTo: window.location.origin } }); if (error) setMessage(error.message) }
  async function logout() { if (!client || busy) return; await client.auth.signOut(); setToken(''); setAccountEmail(''); setMessage('로그아웃되었습니다.') }
  async function fetchMetadata() { if (!url.trim()) return setMessage('YouTube 영상 URL을 입력하세요.'); if (!isYouTubeVideoUrl(url.trim())) return setMessage('올바른 YouTube 영상 URL을 입력하세요.'); const requestedUrl = url.trim(); const currentMetadata = metadata || displayedMetadataRef.current; shouldAnimateMetadataRef.current = !currentMetadata; setBusy(true); if (!metadata && currentMetadata) setMetadata(currentMetadata); setMessage('yt-dlp로 영상 메타데이터를 확인하는 중입니다.'); try { const response = await fetch('/api/youtube/metadata', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: requestedUrl }) }); const body = await response.json().catch(() => ({})); if (!response.ok) throw new Error(body.detail || '영상 메타데이터를 가져오지 못했습니다.'); if (displayedMetadataUrlRef.current !== requestedUrl) setMetadataTab('overview'); setMaterialSelections({ comments: Boolean(body.comment_count), chat: Boolean(body.chat_replay_available), subtitles: Boolean(body.subtitles_available), captions: Boolean(body.captions_available) }); setSubtitleLanguage(body.subtitle_languages?.[0]?.value || ''); setCaptionLanguage(body.caption_languages?.[0]?.value || ''); setMaterials([]); setMaterialTab(null); setSettings(current => ({ ...current, target_duration_seconds: Math.min(3600, Math.max(60, Math.round((Number(body.duration_seconds) || 0) / 4))), transcription_source: body.subtitles_available ? 'youtube_subtitle' : body.captions_available ? 'youtube_caption' : 'whisper_api' })); displayedMetadataUrlRef.current = requestedUrl; setMetadata(body); setMessage('영상 정보를 확인했습니다. 추가 자료를 선택하거나 다음으로 진행하세요.') } catch (error) { setMessage(error instanceof Error ? error.message : '영상 정보 확인에 실패했습니다.') } finally { setBusy(false) } }
  async function downloadMaterials() { if (!metadata) return; shouldAnimateMetadataRef.current = false; setBusy(true); setMaterialDownload({ job_id: '', status: 'running', progress: 0, message: '추가 메타데이터 다운로드를 준비하는 중입니다.' }); setMessage('추가 메타데이터 다운로드를 준비하는 중입니다.'); try { const payload = { url: url.trim(), ...materialSelections, subtitle_language: subtitleLanguage, caption_language: captionLanguage }; const response = await fetch('/api/youtube/metadata/materials/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }); const started = await response.json().catch(() => ({})) as MaterialDownloadJob; if (!response.ok) throw new Error((started as { detail?: string }).detail || '추가 메타데이터를 시작하지 못했습니다.'); let current = started; while (current.status === 'running') { setMaterialDownload(current); setMessage(current.message); await new Promise(resolve => window.setTimeout(resolve, 350)); const statusResponse = await fetch(`/api/youtube/metadata/materials/${current.job_id}`); const next = await statusResponse.json().catch(() => ({})) as MaterialDownloadJob; if (!statusResponse.ok) throw new Error((next as { detail?: string }).detail || '추가 메타데이터 상태를 가져오지 못했습니다.'); current = next } setMaterialDownload(current); setMessage(current.message); if (current.status === 'failed') throw new Error(current.error || current.message || '추가 메타데이터 다운로드에 실패했습니다.'); const artifacts = current.result?.artifacts || []; setMaterials(artifacts); setMaterialTab(artifacts[0]?.kind || null); transitionToPhase('materials') } catch (error) { setMessage(error instanceof Error ? error.message : '추가 메타데이터 다운로드에 실패했습니다.') } finally { setMaterialDownload(null); setBusy(false) } }
  async function startAnalysis() { if (!token) return setMessage('Google 로그인이 필요합니다.'); if (!metadata) return setMessage('영상 정보를 먼저 확인하세요.'); const transcriptLanguage = settings.transcription_source === 'youtube_caption' ? captionLanguage : settings.transcription_source === 'youtube_subtitle' ? subtitleLanguage : undefined; if (settings.transcription_source !== 'whisper_api' && !transcriptLanguage) return setMessage('2단계에서 다운로드한 스크립트 언어를 선택하세요.'); setBusy(true); setReview(null); setMessage('AI 분석 작업을 준비하는 중입니다.'); try { const response = await fetch('/api/youtube/edit/start', { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify({ vod_url: url.trim(), ...settings, ...(transcriptLanguage ? { transcript_language: transcriptLanguage } : {}) }) }); const body = await response.json().catch(() => ({})); if (!response.ok) throw new Error(body.detail || '분석을 시작하지 못했습니다.'); setJob(body); setStartedAt(Date.now()); setMessage(body.message) } catch (error) { setMessage(error instanceof Error ? error.message : '분석 시작에 실패했습니다.') } finally { setBusy(false) } }
  async function renderSelection() { if (!job || !token || !review) return; if (['failed', 'cancelled'].includes(job.status)) return setMessage('종료된 작업은 다시 진행할 수 없습니다. 처음부터 새 작업을 시작하세요.'); const segment_ids = selected.map(segment => segment.segment_id); if (!segment_ids.length) return setMessage('한 개 이상의 구간을 선택하세요.'); setBusy(true); setMessage('선택한 구간으로 렌더링을 준비하는 중입니다.'); try { const response = await fetch(`/api/youtube/edit/${job.job_id}/segments`, { method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify({ segment_ids }) }); const body = await response.json().catch(() => ({})); if (!response.ok) throw new Error(body.detail || '렌더링을 시작하지 못했습니다.'); setJob(body); setStartedAt(Date.now()); setMessage(body.message) } catch (error) { setMessage(error instanceof Error ? error.message : '렌더링 시작에 실패했습니다.') } finally { setBusy(false) } }
  async function cancelJob() { if (!job || !token) return; setBusy(true); try { const response = await fetch(`/api/youtube/edit/${job.job_id}/cancel`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } }); const body = await response.json().catch(() => ({})); if (!response.ok) throw new Error(body.detail || '작업을 취소하지 못했습니다.'); setJob(body); setMessage(body.message || '작업 취소를 요청했습니다.'); setStartedAt(null) } catch (error) { setMessage(error instanceof Error ? error.message : '작업 취소에 실패했습니다.') } finally { setBusy(false) } }
  function restart() { transitionToPhase('metadata'); setJob(null); setReview(null); setMaterials([]); setMaterialTab(null); setMessage('영상 정보를 유지한 채 처음 단계로 돌아왔습니다.') }
  function previewSegment(segment: Segment) { const video = sourcePreviewRef.current; if (!video) return; setPreviewEnd(segment.end); const seekAndPlay = () => { video.pause(); const playAfterSeek = () => void video.play(); if (Math.abs(video.currentTime - segment.start) < 0.001) { playAfterSeek(); return } video.addEventListener('seeked', playAfterSeek, { once: true }); video.currentTime = segment.start }; if (video.readyState >= 1) seekAndPlay(); else video.addEventListener('loadedmetadata', seekAndPlay, { once: true }) }

  const settingsNumber = (label: string, key: 'target_duration_seconds' | 'subtitle_font_size', min: number, max: number, step: number, help?: string) => <label className="setting-card"><span>{label}</span><div className="range-input"><input type="range" min={min} max={max} step={step} value={settings[key]} onChange={event => setSetting(key, Number(event.target.value))} /><input type="number" min={min} max={max} step={step} value={settings[key]} onChange={event => setSetting(key, Number(event.target.value))} /></div>{help && <small>{help}</small>}</label>

  return <div className="ave-app" data-phase={phase}>
    <header className="ave-header"><div><p className="eyebrow">Automatic Video Editor</p><h1>업로드 완료 영상 자동 편집</h1><p>영상은 이 PC에서 처리하고 분석 이력과 AI 호출만 AVE 서버와 동기화합니다.</p></div><div className="header-actions"><span className="status-pill">{token ? `${accountEmail || 'Google'} 로그인됨` : 'Google 로그인 필요'}</span>{token ? <button className="ghost" onClick={logout} disabled={busy}>로그아웃</button> : <button onClick={login} disabled={!client || busy}>Google 로그인</button>}<button className="theme-button" aria-label="테마 전환" onClick={() => setTheme(value => value === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? '☀️' : '🌙'}</button></div></header>
    <main className="workflow-shell">
      {phase === 'metadata' && <section className="panel"><Heading index="01" title="영상 URL 확인" text="YouTube 영상 URL을 입력해 공개 메타데이터와 지원 기능을 확인하세요." /><div className="url-control"><input type="url" value={url} onChange={event => setUrl(event.target.value)} placeholder="https://www.youtube.com/watch?v=..." /><button onClick={fetchMetadata} disabled={busy}>정보 확인</button></div><Status text={message} failed={job?.status === 'failed'} />{metadata && <><WorkflowMetadataView metadata={metadata} tab={metadataTab} setTab={setMetadataTab} selections={materialSelections} locked={busy} onSelectionChange={(kind, checked) => setMaterialSelections(current => ({ ...current, [kind]: checked }))} subtitleLanguage={subtitleLanguage} captionLanguage={captionLanguage} onSubtitleLanguageChange={setSubtitleLanguage} onCaptionLanguageChange={setCaptionLanguage} /><div className="phase-actions"><button onClick={downloadMaterials} disabled={busy}>다음: 추가 자료 다운로드</button></div></>}</section>}
      {phase === 'materials' && <section className="panel"><Heading index="02" title="추가 메타데이터 확인" text="1단계에서 선택한 자료를 확인한 뒤 분석 설정으로 이동하세요." /><MaterialPreview artifacts={materials} activeKind={materialTab} onSelect={setMaterialTab} /><Status text={message} failed={job?.status === 'failed'} /><div className="phase-actions"><button className="ghost" onClick={() => transitionToPhase('metadata')}>이전</button><button onClick={() => transitionToPhase('analysis')}>다음: 분석 설정</button></div></section>}
      {phase === 'analysis' && <section className="panel"><Heading index="03" title="영상 분석 및 편집 후보 만들기" text="2단계에서 준비한 스크립트를 재사용해 챕터별 편집 후보를 만듭니다." /><div className="settings-grid"><label className="setting-card"><span>LLM 엔진</span><CustomSelect ariaLabel="LLM 엔진" value={settings.llm_provider} options={[{ value: 'deepseek', label: 'DeepSeek' }, { value: 'gemini', label: 'Gemini' }]} onChange={value => setSetting('llm_provider', value)} /></label><label className="setting-card"><span>스크립트 소스</span><CustomSelect ariaLabel="스크립트 소스" value={settings.transcription_source} options={scriptSourceOptions} onChange={value => setSetting('transcription_source', value)} /></label>{settingsNumber('목표 길이(초)', 'target_duration_seconds', 60, 3600, 10)}{settingsNumber('자막 크기', 'subtitle_font_size', 8, 64, 1)}<label className="setting-card"><span>렌더링 방식</span><CustomSelect ariaLabel="렌더링 방식" value={settings.render_mode} options={[{ value: 'preview', label: 'preview' }, { value: 'exact', label: 'exact' }]} onChange={value => setSetting('render_mode', value)} /></label><label className="setting-card"><span>자막 글꼴</span><input value={settings.subtitle_font_name} onChange={event => setSetting('subtitle_font_name', event.target.value)} /></label>{settings.transcription_source === 'whisper_api' && <><label className="setting-card"><span>Whisper 언어</span><input value={settings.stt_language} onChange={event => setSetting('stt_language', event.target.value)} /></label><label className="setting-card"><span>Whisper 재생 배속</span><CustomSelect ariaLabel="Whisper 재생 배속" value={String(settings.stt_speed) as '1' | '1.5' | '2'} options={[{ value: '1', label: '1.0 (품질)' }, { value: '1.5', label: '1.5 (균형)' }, { value: '2', label: '2.0 (속도)' }]} onChange={value => setSetting('stt_speed', Number(value))} /></label><label className="setting-card full"><span>Whisper 초기 프롬프트</span><input value={settings.stt_initial_prompt} onChange={event => setSetting('stt_initial_prompt', event.target.value)} /></label><label className="setting-card full"><span>Whisper 핫워드</span><input value={settings.stt_hotwords} onChange={event => setSetting('stt_hotwords', event.target.value)} placeholder="쉼표로 구분" /></label></>}</div><Status text={message} failed={job?.status === 'failed'} /><div className="phase-actions"><button className="ghost" onClick={() => transitionToPhase('materials')}>이전</button><button onClick={startAnalysis} disabled={busy || !metadata || !token}>다음: AI 분석 시작</button></div></section>}
      {phase === 'review' && review && <section className="panel"><Heading index="04" title="구간 검토 및 영상 생성" text="챕터 요약을 확인하고 포함할 섹션을 직접 선택하세요." /><div className="review-layout"><div className="preview-card"><h3>원본 구간 미리보기</h3><video ref={sourcePreviewRef} controls src={'/api/youtube/edit/' + (job?.job_id || '') + '/media/source'} onTimeUpdate={event => { if (previewEnd !== null && event.currentTarget.currentTime >= previewEnd) { event.currentTarget.pause(); setPreviewEnd(null) } }} /></div><div><div className="review-toolbar"><strong>{selected.length}개 선택 · 예상 길이 {formatTime(selectedDuration)}</strong><div><button className="ghost compact" onClick={() => updateSelection(new Set(review.recommended_segment_ids))}>AI 추천</button><button className="ghost compact" onClick={() => updateSelection(new Set(review.chapters.flatMap(chapter => chapter.sections.map(section => section.section_id))))}>전체 선택</button><button className="ghost compact" onClick={() => updateSelection(new Set())}>전체 해제</button></div></div><h3>챕터 · 섹션 선택</h3><div className="chapter-list">{review.chapters.map((chapter, chapterIndex) => { const allSelected = chapter.sections.length > 0 && chapter.sections.every(section => section.selected); const opened = openChapters.has(chapter.chapter_id); return <article className="chapter" key={chapter.chapter_id}><div className="chapter-head"><input type="checkbox" checked={allSelected} onChange={event => { const ids = new Set(selected.map(section => section.section_id)); chapter.sections.forEach(section => event.target.checked ? ids.add(section.section_id) : ids.delete(section.section_id)); updateSelection(ids) }} /><div><b>챕터 {chapterIndex + 1}<span className="header-separator">·</span><DetailedTime value={chapter.start} />–<DetailedTime value={chapter.end} /><span className="header-separator">·</span>섹션 {chapter.sections.length}개 <em className="score-badge">LLM 점수 {Math.round(chapter.llm_score)}</em></b><p>{chapter.summary}</p></div><button className="ghost compact" onClick={() => setOpenChapters(current => { const next = new Set(current); opened ? next.delete(chapter.chapter_id) : next.add(chapter.chapter_id); return next })}>{opened ? '접기' : '세부 구간'}</button></div>{opened && <div className="section-list">{chapter.sections.map((section, sectionIndex) => <label className={section.selected ? 'segment selected' : 'segment'} key={section.section_id}><input type="checkbox" checked={section.selected} onChange={() => { const ids = new Set(selected.map(item => item.section_id)); ids.has(section.section_id) ? ids.delete(section.section_id) : ids.add(section.section_id); updateSelection(ids) }} /><span><b>섹션 {sectionIndex + 1}<span className="header-separator">·</span><DetailedTime value={section.start} />–<DetailedTime value={section.end} /> {section.llm_score !== undefined && <em className="score-badge">LLM 점수 {Math.round(section.llm_score)}</em>}</b><br />{section.text}</span><button type="button" className="ghost compact" onClick={() => previewSegment({ ...section, segment_id: section.section_id, chapter_id: chapter.chapter_id })}>미리보기</button></label>)}</div>}</article> })}</div></div></div><Status text={message} failed={job?.status === 'failed'} /><div className="phase-actions"><button className="ghost" onClick={() => transitionToPhase('analysis')}>이전</button><button onClick={renderSelection} disabled={busy || !selected.length}>다음: 선택 구간 렌더링</button></div></section>}
      {phase === 'render' && job && <section className="panel"><Heading index="05" title="완료된 결과 영상" text="선택한 구간으로 생성된 최종 영상을 조회하고 저장할 수 있습니다." /><div className="preview-card"><h3>최종 편집 결과</h3><video controls src={`/api/youtube/edit/${job.job_id}/media/rendered`} /><a href={`/api/youtube/edit/${job.job_id}/media/rendered`} download>영상 저장</a></div><Status text={message} failed={job.status === 'failed'} /><div className="phase-actions"><button className="ghost" onClick={restart}>처음</button></div></section>}
    </main>
    <aside className="progress-dock" data-state={job?.status || 'idle'}><div className="progress-main"><b>{progressLabel}</b><div className="progress-track"><span style={{ width: `${progressValue}%` }} /></div></div><strong>{progressValue}%</strong><time>{startedAt ? formatTime(elapsed) : ''}</time>{job && !['completed', 'failed', 'cancelled', 'awaiting_selection'].includes(job.status) && <button className="ghost compact cancel-job-button" onClick={cancelJob} disabled={busy}>작업 취소</button>}<p>{message}</p><FooterActions phase={phase} metadataReady={Boolean(metadata)} token={Boolean(token)} busy={controlsLocked} hasSelection={selected.length > 0} hasRenderedResult={Boolean(job?.result?.revision)} onPhase={transitionToPhase} onDownloadMaterials={downloadMaterials} onStart={startAnalysis} onRender={renderSelection} onRestart={restart} /></aside>
  </div>
}

function Heading({ index, title, text }: { index: string; title: string; text: string }) { return <div className="heading"><span>{index}</span><div><h2>{title}</h2><p>{text}</p></div></div> }
function CustomSelect<T extends string>({ ariaLabel, value, options, onChange, className, disabled = false }: { ariaLabel: string; value: T; options: { value: T; label: string }[]; onChange: (value: T) => void; className?: string; disabled?: boolean }) {
  const [open, setOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const selected = options.find(option => option.value === value) || options[0]
  useEffect(() => { if (disabled) setOpen(false) }, [disabled])

  useEffect(() => {
    const close = (event: PointerEvent) => { if (!menuRef.current?.contains(event.target as Node)) setOpen(false) }
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', onKeyDown)
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', onKeyDown) }
  }, [])

  return <div className={`select-menu ${className || ''}`} ref={menuRef}><button type="button" className="select-trigger ghost" aria-label={ariaLabel} aria-haspopup="listbox" aria-expanded={open} disabled={disabled} onClick={() => setOpen(current => !current)}><span>{selected.label}</span><svg className="select-chevron" aria-hidden="true" viewBox="0 0 16 16"><path d="m4 6 4 4 4-4" /></svg></button>{open && <div className="select-options" role="listbox" aria-label={ariaLabel}>{options.map(option => <button type="button" role="option" aria-selected={option.value === value} className={option.value === value ? 'select-option selected' : 'select-option'} key={option.value} disabled={disabled} onClick={() => { onChange(option.value); setOpen(false) }}>{option.label}</button>)}</div>}</div>
}
function Status({ text, failed }: { text: string; failed?: boolean }) { return <p className={failed ? 'status-message failed' : 'status-message'} role="status">{text}</p> }
function MaterialPreview({ artifacts, activeKind, onSelect }: { artifacts: MaterialArtifact[]; activeKind: MaterialKind | null; onSelect: (kind: MaterialKind) => void }) {
  const active = artifacts.find(artifact => artifact.kind === activeKind) || artifacts[0]
  if (!active) return <section className="material-preview empty"><p>선택하여 다운로드한 추가 메타데이터가 없습니다.</p></section>
  const rows = Array.isArray(active.preview) ? active.preview as Record<string, unknown>[] : []
  const columns = active.kind === 'comments' ? [['author', '작성자'], ['text', '댓글'], ['like_count', '좋아요'], ['_time_text', '작성 시점']] : active.kind === 'chat' ? [['author', '작성자'], ['message', '메시지'], ['elapsed_seconds', '시각']] : [['start', '시작'], ['end', '종료'], ['text', '자막']]
  const countText = active.kind === 'comments' && active.total_count !== undefined && active.total_count !== null ? `${active.count ?? 0} / ${active.total_count}개` : active.count !== null ? `${active.count}개` : ''
  return <section className="material-preview"><nav>{artifacts.map(artifact => <button key={artifact.kind} className={artifact.kind === active.kind ? 'active' : 'ghost'} onClick={() => onSelect(artifact.kind)}>{artifact.label}</button>)}</nav><div className="material-preview-head"><p><b>{active.label}</b>{countText ? ` · ${countText}` : ''}</p>{active.kind === 'comments' && <p className="material-preview-note">※ 대댓글은 표시하지 않습니다. 좋아요 수를 우선하고, 같으면 최신 댓글부터 정렬합니다.</p>}</div><div className={`material-table-wrap material-table-${active.kind}`}><table><thead><tr><th>#</th>{columns.map(([key, label]) => <th key={key}>{label}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={index}><td>{index + 1}</td>{columns.map(([key]) => <td key={key}>{String(row[key] ?? '')}</td>)}</tr>)}</tbody></table></div></section>
}
function FooterActions({ phase, metadataReady, token, busy, hasSelection, hasRenderedResult, onPhase, onDownloadMaterials, onStart, onRender, onRestart }: { phase: Phase; metadataReady: boolean; token: boolean; busy: boolean; hasSelection: boolean; hasRenderedResult: boolean; onPhase: (phase: Phase) => void; onDownloadMaterials: () => void; onStart: () => void; onRender: () => void; onRestart: () => void }) {
  void hasRenderedResult
  const changePhase = (next: Phase) => onPhase(next)
  if (phase === 'metadata') return <div className="footer-actions"><button title="다음: 추가 자료 다운로드" aria-label="다음: 추가 자료 다운로드" onClick={onDownloadMaterials} disabled={busy || !metadataReady}>다음</button></div>
  if (phase === 'materials') return <div className="footer-actions"><button className="ghost" title="이전: 영상 URL 확인" aria-label="이전: 영상 URL 확인" onClick={() => changePhase('metadata')} disabled={busy}>이전</button><button title="다음: 분석 설정" aria-label="다음: 분석 설정" onClick={() => changePhase('analysis')} disabled={busy}>다음</button></div>
  if (phase === 'analysis') return <div className="footer-actions"><button className="ghost" title="이전: 추가 자료 확인" aria-label="이전: 추가 자료 확인" onClick={() => changePhase('materials')} disabled={busy}>이전</button><button title="다음: AI 분석 시작" aria-label="다음: AI 분석 시작" onClick={onStart} disabled={busy || !metadataReady || !token}>다음</button></div>
  if (phase === 'review') return <div className="footer-actions"><button className="ghost" title="이전: 분석 설정" aria-label="이전: 분석 설정" onClick={() => changePhase('analysis')} disabled={busy}>이전</button><button title="다음: 선택 구간 렌더링" aria-label="다음: 선택 구간 렌더링" onClick={onRender} disabled={busy || !hasSelection}>다음</button></div>
  return <div className="footer-actions"><button className="ghost" title="이전: 구간 검토" aria-label="이전: 구간 검토" onClick={() => changePhase('review')} disabled={busy}>이전</button><button className="ghost" title="처음 단계로 돌아가기" aria-label="처음 단계로 돌아가기" onClick={onRestart} disabled={busy}>처음</button></div>
}
function HeatmapChart({ metadata, peak }: { metadata: Metadata; peak: number }) {
  return <><div className="heatmap">{(metadata.heatmap || []).map((item, index) => <span key={index} style={{ height: `${Math.max(3, ((item.value || 0) / peak) * 100)}%` }} />)}</div><div className="heatmap-axis" aria-label="히트맵 시간 축">{[.125, .25, .375, .5, .625, .75, .875, 1].map(ratio => <span key={ratio} style={ratio === 1 ? { right: 0, transform: 'none' } : { left: `${ratio * 100}%` }}>{formatTime((metadata.duration_seconds || 0) * ratio)}</span>)}</div></>
}

function WorkflowMetadataView({ metadata, tab, setTab, selections, locked, onSelectionChange, subtitleLanguage, captionLanguage, onSubtitleLanguageChange, onCaptionLanguageChange }: { metadata: Metadata; tab: 'overview' | 'description' | 'chapters' | 'heatmap'; setTab: (value: 'overview' | 'description' | 'chapters' | 'heatmap') => void; selections: Record<MaterialKind, boolean>; locked: boolean; onSelectionChange: (kind: MaterialKind, checked: boolean) => void; subtitleLanguage: string; captionLanguage: string; onSubtitleLanguageChange: (value: string) => void; onCaptionLanguageChange: (value: string) => void }) {
  const files = metadata.thumbnail_files?.length ? metadata.thumbnail_files : metadata.thumbnail ? [{ url: metadata.thumbnail, is_primary: true }] : []
  const orderedFiles = [...files].sort((left, right) => Number(Boolean(right.is_primary)) - Number(Boolean(left.is_primary)))
  const [thumbnailIndex, setThumbnailIndex] = useState(0)
  const peak = Math.max(1, ...(metadata.heatmap || []).map(item => item.value || 0))
  const availability = (value?: boolean) => value === true ? '지원' : value === false ? '미지원' : '확인 불가'
  const rows: [string, string][] = [
    ['채널', metadata.channel || '확인 불가'], ['길이', formatTime(metadata.duration_seconds)], ['날짜', formatDate(metadata.upload_date)], ['조회수', metadata.view_count?.toLocaleString() || '확인 불가'], ['좋아요', metadata.like_count?.toLocaleString() || '확인 불가'], ['댓글', metadata.comment_count?.toLocaleString() || '확인 불가'], ['채팅', availability(metadata.chat_replay_available)], ['자막', availability(metadata.subtitles_available)], ['캡션', availability(metadata.captions_available)], ['ID', metadata.video_id || '확인 불가'], ['카테고리', metadata.categories?.join(', ') || '없음'], ['태그', metadata.tags?.join(', ') || '없음'],
  ]
  const materialControl = (label: string) => {
    const details: Record<string, { kind: MaterialKind; enabled: boolean; languages?: LanguageOption[]; language?: string; onLanguageChange?: (value: string) => void }> = {
      '댓글': { kind: 'comments', enabled: Boolean(metadata.comment_count) },
      '채팅': { kind: 'chat', enabled: Boolean(metadata.chat_replay_available) },
      '자막': { kind: 'subtitles', enabled: Boolean(metadata.subtitles_available), languages: metadata.subtitle_languages, language: subtitleLanguage, onLanguageChange: onSubtitleLanguageChange },
      '캡션': { kind: 'captions', enabled: Boolean(metadata.captions_available), languages: metadata.caption_languages, language: captionLanguage, onLanguageChange: onCaptionLanguageChange },
    }
    const detail = details[label]
    if (!detail) return null
    return <span className="metadata-material-control" onClick={event => event.stopPropagation()}>{detail.languages?.length ? <CustomSelect ariaLabel={`${label} 언어`} className="metadata-language-select" value={detail.language || ''} options={detail.languages} disabled={locked || !detail.enabled} onChange={value => detail.onLanguageChange?.(value)} /> : null}<input type="checkbox" aria-label={`${label} 다운로드`} checked={selections[detail.kind]} disabled={locked || !detail.enabled} onChange={event => onSelectionChange(detail.kind, event.target.checked)} /></span>
  }
  const toggleMaterial = (label: string) => {
    const details: Record<string, { kind: MaterialKind; enabled: boolean }> = {
      '댓글': { kind: 'comments', enabled: Boolean(metadata.comment_count) }, '채팅': { kind: 'chat', enabled: Boolean(metadata.chat_replay_available) }, '자막': { kind: 'subtitles', enabled: Boolean(metadata.subtitles_available) }, '캡션': { kind: 'captions', enabled: Boolean(metadata.captions_available) },
    }
    const detail = details[label]
    if (!locked && detail?.enabled) onSelectionChange(detail.kind, !selections[detail.kind])
  }
  const currentThumbnailIndex = Math.min(thumbnailIndex, Math.max(0, orderedFiles.length - 1))
  const thumbnail = orderedFiles[currentThumbnailIndex]
  useEffect(() => setThumbnailIndex(0), [metadata.video_id])
  return <div className="metadata-card legacy-metadata"><nav>{(['overview', 'description', 'chapters', 'heatmap'] as const).map(value => <button className={tab === value ? 'active' : 'ghost'} key={value} onClick={() => setTab(value)}>{({ overview: '개요', description: '설명', chapters: '챕터', heatmap: '히트맵' } as const)[value]}</button>)}</nav>{tab === 'overview' && <div className="metadata-overview"><div className="thumbnail-viewer">{thumbnail && <a className="thumbnail-image" href={thumbnail.source_url || thumbnail.url} target="_blank" rel="noreferrer"><img src={thumbnail.url} alt={`${metadata.title || '영상'} 썸네일 ${currentThumbnailIndex + 1}`} /></a>}<div className="thumbnail-controls">{orderedFiles.length > 1 && <button type="button" className="thumbnail-arrow ghost" aria-label="이전 썸네일" title="이전 썸네일" onClick={() => setThumbnailIndex(index => index === 0 ? orderedFiles.length - 1 : index - 1)}><svg aria-hidden="true" viewBox="0 0 16 16"><path d="m10 3-5 5 5 5" /></svg></button>}<p className="thumbnail-position" aria-live="polite">{orderedFiles.length ? `${currentThumbnailIndex + 1} / ${orderedFiles.length}` : '0 / 0'}</p>{orderedFiles.length > 1 && <button type="button" className="thumbnail-arrow ghost" aria-label="다음 썸네일" title="다음 썸네일" onClick={() => setThumbnailIndex(index => index >= orderedFiles.length - 1 ? 0 : index + 1)}><svg aria-hidden="true" viewBox="0 0 16 16"><path d="m6 3 5 5-5 5" /></svg></button>}</div></div><div><h3>{metadata.title || '제목 없음'}</h3><p className="metadata-material-guide">체크박스를 선택해 추가 다운로드할 자료를 선택하세요.</p><dl>{rows.map(([label, value]) => <div className={materialControl(label) ? "material-metadata-cell" : ""} key={label} onClick={() => toggleMaterial(label)}><dt>{label}</dt><dd>{value}{materialControl(label)}</dd></div>)}</dl></div></div>}{tab === 'description' && <p className="metadata-text">{metadata.description || '설명이 없습니다.'}</p>}{tab === 'chapters' && <table className="metadata-table"><thead><tr><th>시작</th><th>종료</th><th>제목</th></tr></thead><tbody>{(metadata.chapters || []).map((item, index) => <tr key={index}><td>{formatTime(item.start_time)}</td><td>{formatTime(item.end_time)}</td><td>{item.title || '제목 없음'}</td></tr>)}</tbody></table>}{tab === 'heatmap' && <HeatmapChart metadata={metadata} peak={peak} />}</div>
}
