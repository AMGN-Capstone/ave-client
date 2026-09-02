import { useEffect, useState } from 'react'
import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import './index.css'
import AppView from './AppView'

type EditJob = { job_id: string; status: string; progress: number; message: string; error?: string }
type Segment = { segment_id: string; start: number; end: number; text: string; final_score?: number; selected: boolean }

function LegacyApp() {
  const [client, setClient] = useState<SupabaseClient | null>(null)
  const [sessionToken, setSessionToken] = useState('')
  const [url, setUrl] = useState('')
  const [message, setMessage] = useState('설정을 불러오는 중입니다.')
  const [job, setJob] = useState<EditJob | null>(null)
  const [segments, setSegments] = useState<Segment[]>([])
  const [subtitles, setSubtitles] = useState('')

  useEffect(() => {
    fetch('/api/config').then(async response => {
      if (!response.ok) throw new Error('Supabase 설정이 없습니다.')
      return response.json()
    }).then(config => {
      const next = createClient(config.supabase_url, config.supabase_anon_key)
      setClient(next)
      return next.auth.getSession()
    }).then(({ data }) => {
      setSessionToken(data.session?.access_token || '')
      setMessage(data.session ? '로그인되었습니다.' : 'Google 로그인이 필요합니다.')
    }).catch(error => setMessage(error.message))
  }, [])

  useEffect(() => {
    if (!job || ['completed', 'failed', 'awaiting_selection'].includes(job.status)) return
    const timer = window.setInterval(async () => {
      const response = await fetch(`/api/youtube/edit/status/${job.job_id}`)
      if (response.ok) {
        const next = await response.json() as EditJob
        setJob(next)
        setMessage(next.message)
      }
    }, 1500)
    return () => window.clearInterval(timer)
  }, [job])

  useEffect(() => {
    if (!job || job.status !== 'completed') return
    fetch(`/api/youtube/edit/${job.job_id}/subtitles`).then(response => response.ok ? response.json() : Promise.reject()).then(data => setSubtitles(data.content || '')).catch(() => setMessage('자막을 불러오지 못했습니다.'))
  }, [job])

  useEffect(() => {
    if (!job || job.status !== 'awaiting_selection') return
    fetch(`/api/youtube/edit/${job.job_id}/segments`).then(response => response.ok ? response.json() : Promise.reject()).then(data => setSegments(data.segments || [])).catch(() => setMessage('후보 구간을 불러오지 못했습니다.'))
  }, [job])

  async function login() {
    if (!client) return
    const { error } = await client.auth.signInWithOAuth({ provider: 'google', options: { redirectTo: window.location.origin } })
    if (error) setMessage(error.message)
  }

  async function startAnalysis() {
    if (!sessionToken) return setMessage('Google 로그인이 필요합니다.')
    if (!url.trim()) return setMessage('YouTube 영상 URL을 입력하세요.')
    const response = await fetch('/api/youtube/edit/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${sessionToken}` },
      body: JSON.stringify({ vod_url: url, interactive_selection: true }),
    })
    const body = await response.json().catch(() => ({})) as EditJob & { detail?: string }
    if (!response.ok) return setMessage(body.detail || '분석을 시작하지 못했습니다.')
    setJob(body)
    setMessage(body.message)
  }

  function toggleSegment(segmentId: string) {
    setSegments(current => current.map(segment => segment.segment_id === segmentId ? { ...segment, selected: !segment.selected } : segment))
  }

  async function renderSelection() {
    if (!job || !sessionToken) return
    const selected = segments.filter(segment => segment.selected).map(segment => segment.segment_id)
    if (!selected.length) return setMessage('한 개 이상의 구간을 선택하세요.')
    const response = await fetch(`/api/youtube/edit/${job.job_id}/segments`, { method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${sessionToken}` }, body: JSON.stringify({ segment_ids: selected }) })
    const body = await response.json().catch(() => ({})) as EditJob & { detail?: string }
    if (!response.ok) return setMessage(body.detail || '렌더링을 시작하지 못했습니다.')
    setJob(body); setMessage(body.message)
  }

  async function saveSubtitles() {
    if (!job) return
    const response = await fetch(`/api/youtube/edit/${job.job_id}/subtitles`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: subtitles }) })
    const body = await response.json().catch(() => ({})) as { message?: string; detail?: string }
    setMessage(response.ok ? body.message || '자막을 저장했습니다.' : body.detail || '자막 저장에 실패했습니다.')
  }

  return <main className="mx-auto min-h-screen max-w-3xl p-6 text-slate-900">
    <h1 className="text-3xl font-bold">AVE 자동 영상 편집</h1>
    <p className="mt-2 text-slate-600">영상은 내 PC에서 처리하고, 분석 이력은 AVE 서버에 안전하게 기록합니다.</p>
    <section className="mt-8 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <p className="mb-4">{message}</p>
      <button className="rounded bg-sky-600 px-4 py-2 font-medium text-white" onClick={login}>Google 로그인</button>
      <div className="mt-5 flex gap-2">
        <input className="min-w-0 flex-1 rounded border border-slate-300 px-3 py-2" value={url} onChange={event => setUrl(event.target.value)} placeholder="YouTube 영상 URL" />
        <button className="rounded bg-slate-900 px-4 py-2 font-medium text-white" onClick={startAnalysis}>분석 시작</button>
      </div>
    </section>
    {job && <section className="mt-5 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <p className="font-medium">진행 상태: {job.status} · {job.progress}%</p>
      <p className="mt-2 text-slate-600">{job.message}</p>
      {job.error && <p className="mt-2 text-red-600">{job.error}</p>}
    </section>}
    {job?.status === 'completed' && <section className="mt-5 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-xl font-bold">편집 결과</h2>
      <video className="mt-4 w-full rounded bg-black" controls src={`/api/youtube/edit/${job.job_id}/media/rendered`} />
      <h3 className="mt-5 font-bold">자막 편집</h3>
      <textarea className="mt-2 h-56 w-full rounded border border-slate-300 p-3 font-mono text-sm" value={subtitles} onChange={event => setSubtitles(event.target.value)} />
      <button className="mt-2 rounded bg-slate-900 px-4 py-2 font-medium text-white" onClick={saveSubtitles}>자막 저장 및 재렌더링</button>
    </section>}
    {job?.status === 'awaiting_selection' && <section className="mt-5 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-xl font-bold">추천 구간 검토</h2>
      <p className="mt-1 text-sm text-slate-600">선택한 구간만 로컬에서 렌더링합니다.</p>
      <video className="mt-4 w-full rounded bg-black" controls src={`/api/youtube/edit/${job.job_id}/media/source`} />
      <div className="mt-4 space-y-2">{segments.map(segment => <label key={segment.segment_id} className="flex cursor-pointer gap-3 rounded border p-3"><input type="checkbox" checked={segment.selected} onChange={() => toggleSegment(segment.segment_id)} /><span><b>{Math.floor(segment.start)}초–{Math.floor(segment.end)}초</b> {segment.final_score !== undefined && `· 점수 ${segment.final_score}`}<br />{segment.text}</span></label>)}</div>
      <button className="mt-4 rounded bg-slate-900 px-4 py-2 font-medium text-white" onClick={renderSelection}>선택 구간 렌더링</button>
    </section>}
  </main>
}

export { LegacyApp }
export default AppView
