import { useState, useEffect, useRef } from 'react'
import './App.css'
import BackgroundAnimation from './BackgroundAnimation';

function App() {
  const [url, setUrl] = useState('')
  const [logs, setLogs] = useState([])
  const [status, setStatus] = useState('idle') // idle, running, completed, error
  // progress can be object { current, total } or just handled as percentage
  const [progress, setProgress] = useState({ current: 0, total: 100 })
  const [pdfFile, setPdfFile] = useState(null)

  // WebSocket state
  const [ws, setWs] = useState(null)
  const logContainerRef = useRef(null)

  // Determine API URL based on environment
  // In dev (Vite Proxy) and production (Nginx), we use relative paths.
  const IS_SECURE = window.location.protocol === 'https:';
  const WS_PROTOCOL = IS_SECURE ? 'wss:' : 'ws:';
  const WS_URL = `${WS_PROTOCOL}//${window.location.host}/ws`;

  useEffect(() => {
    // Connect to WebSocket using relative path (via host)
    const socket = new WebSocket(WS_URL);

    socket.onopen = () => {
      console.log('Connected to WebSocket')
    }

    socket.onmessage = (event) => {
      const data = JSON.parse(event.data)

      if (data.type === 'log') {
        setLogs(prev => [...prev, data.message])
      } else if (data.type === 'progress') {
        setProgress({ current: data.current, total: data.total })
      } else if (data.type === 'status') {
        setStatus(data.status)
        if (data.status === 'completed' && data.filename) {
          setPdfFile(data.filename)
        }
      } else if (data.type === 'error') {
        setStatus('error')
        setLogs(prev => [...prev, `ERROR: ${data.message}`])
      }
    }

    socket.onclose = () => {
      console.log('Disconnected')
    }

    setWs(socket)

    return () => {
      socket.close()
    }
  }, [])

  useEffect(() => {
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight
    }
  }, [logs])

  const handleStart = () => {
    if (!url) return
    setLogs([])
    setProgress({ current: 0, total: 100 })
    setStatus('running')
    setPdfFile(null)
    if (ws) {
      ws.send(JSON.stringify({ command: 'start', url }))
    }
  }

  const handleCancel = () => {
    if (ws) {
      ws.send(JSON.stringify({ command: 'cancel' }))
    }
  }

  // Calculate percentage
  const percentage = progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : 0;

  return (
    <div className="container py-5">
      <BackgroundAnimation />
      <div className="row justify-content-center">
        <div className="col-lg-8 col-md-10">

          <div className="card shadow-lg border-0 bg-dark text-light glass-card">
            <div className="card-body p-4 p-md-5">

              <div className="text-center mb-4">
                <h1 className="display-5 fw-bold title-animate">
                  Manga Downloader
                </h1>
              </div>

              {/* URL Input */}
              <div className="input-group mb-4 animate-fade-in" style={{ animationDelay: '0.1s' }}>
                <span className="input-group-text bg-secondary border-0 text-light">🔗</span>
                <input
                  type="text"
                  className="form-control form-control-lg bg-black border-0 text-light placeholder-muted"
                  placeholder="Pegar URL aquí (TMO, M440, H2R...)"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  disabled={status === 'running'}
                />
              </div>

              {/* Buttons */}
              <div className="d-grid gap-2 d-md-flex justify-content-md-end mb-4 animate-fade-in" style={{ animationDelay: '0.2s' }}>
                <button
                  className="btn btn-danger btn-lg px-4 btn-animate d-inline-flex align-items-center justify-content-center"
                  onClick={handleCancel}
                  disabled={status !== 'running'}
                >
                  <i className="bi bi-stop-circle me-2"></i>Cancelar
                </button>
                <button
                  className="btn btn-primary btn-lg px-5 neon-button btn-animate d-inline-flex align-items-center justify-content-center"
                  onClick={handleStart}
                  disabled={status === 'running' || !url}
                >
                  {status === 'running' ? (
                    <>
                      <span className="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>
                      Descargando...
                    </>
                  ) : (
                    '⬇ Descargar PDF'
                  )}
                </button>
              </div>

              {/* Progress Bar */}
              {status === 'running' && (
                <div className="mb-4 animate-fade-in">
                  <div className="d-flex justify-content-between mb-1">
                    <span>Progreso</span>
                    <span>{percentage}% ({progress.current}/{progress.total})</span>
                  </div>
                  <div className="progress bg-secondary" style={{ height: '10px' }}>
                    <div
                      className="progress-bar bg-success progress-bar-striped progress-bar-animated"
                      role="progressbar"
                      style={{ width: `${percentage}%` }}
                    ></div>
                  </div>
                </div>
              )}

              {/* Log Window */}
              <div
                className="card bg-black border-secondary mb-4 overflow-auto log-window animate-fade-in"
                style={{ height: '300px', animationDelay: '0.3s' }}
                ref={logContainerRef}
              >
                <div className="card-body p-3 font-monospace small text-success">
                  {logs.length === 0 ? (
                    <span className="text-muted fst-italic">Esperando logs...</span>
                  ) : (
                    logs.map((log, index) => (
                      <div key={index} className="mb-1 text-break log-entry-animate" style={{ animationDelay: `${index * 0.05}s` }}>
                        <span className="text-muted mr-2">[{new Date().toLocaleTimeString()}]</span> {log}
                      </div>
                    ))
                  )}
                </div>
              </div>

              {/* Success / Download Area */}
              {status === 'completed' && (
                <div className="alert alert-success border-success bg-opacity-10 text-center animate-fade-in">
                  <h4 className="alert-heading mb-3">✅ ¡Proceso Completado!</h4>
                  {pdfFile && (
                    <div className="mt-3">
                      <a
                        href={`/pdfs/${pdfFile}`}
                        className="btn btn-success btn-lg shadow-sm"
                        download
                      >
                        📥 Guardar PDF en PC
                      </a>
                      <div className="form-text mt-2 text-success-emphasis">
                        El archivo está listo para descargar.
                      </div>
                    </div>
                  )}
                </div>
              )}

              {status === 'error' && (
                <div className="alert alert-danger text-center">
                  ❌ Ocurrió un error. Revisa los logs para más detalles.
                </div>
              )}

            </div>
          </div>

          <div className="text-center mt-4 text-muted footer-text animate-fade-in" style={{ animationDelay: '0.4s' }}>
            <p className="mb-0 fw-bold signature-text" style={{ letterSpacing: '2px', fontSize: '1.1rem' }}>vibecoded by xWolz</p>
            <small style={{ fontSize: '0.75rem', opacity: 0.7 }}>
              &copy; 2026 All rights reserved.
            </small>
          </div>

        </div>
      </div>
    </div>
  )
}

export default App
