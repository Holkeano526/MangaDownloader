"use client";

import { useState, useEffect, useRef } from 'react';
import { Toaster, sileo } from 'sileo';
import BackgroundAnimation from '@/components/BackgroundAnimation';
import { AnimatedSpinner, AnimatedSuccess, AnimatedError } from '@/components/SvgAnimations';

export default function Home() {
    const [url, setUrl] = useState('');
    const [queue, setQueue] = useState([]);
    const [ws, setWs] = useState(null);
    const logContainerRef = useRef(null);
    const currentProcessingUrl = useRef(null);

    useEffect(() => {
        // Notify that system is ready
        setTimeout(() => {
            sileo.success({ title: 'Sistema Listo', description: 'Web lista para recibir el enlace del manga.' });
        }, 500);

        const IS_SECURE = window.location.protocol === 'https:';
        const WS_PROTOCOL = IS_SECURE ? 'wss:' : 'ws:';
        // If running Next.js dev server on 3000, but websockets are on backend (e.g. 80 / 8000). 
        // Usually via proxy or direct. Using window.location.host assumes they are served together.
        // For local dev with vite it was proxied. With Next.js we need to ensure the same or point explicitly.
        // Let's keep the dynamic host approach since Nginx serves it in production.
        const WS_URL = `${WS_PROTOCOL}//${window.location.host}/ws`;

        const socket = new WebSocket(WS_URL);

        socket.onopen = () => {
            console.log('Connected to WebSocket');
        };

        socket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'queue_state') {
                    setQueue(data.queue);
                } else if (data.type === 'error') {
                    sileo.error({ title: '¡Error!', description: data.message || 'Ha ocurrido un error inesperado.' });
                }
            } catch (e) {
                console.error("Failed to parse websocket message", e);
            }
        };

        socket.onclose = () => {
            console.log('Disconnected');
        };

        setWs(socket);

        return () => {
            socket.close();
        };
    }, []);

    const handleStart = () => {
        if (!url) return;
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ command: 'start', url }));
            setUrl('');
            sileo.info({ title: 'Añadido a la cola', description: 'El manga se procesará en breve.' });
        } else {
            sileo.error({ title: 'Error de conexión', description: "No hay conexión con el servidor. Recarga la página." });
        }
    };

    const handleCancel = (id) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ command: 'cancel', id }));
        }
    };

    return (
        <main
            className="min-h-screen py-10 relative overflow-hidden flex flex-col items-center justify-center"
            style={{
                backgroundImage: 'linear-gradient(rgba(0, 0, 0, 0.7), rgba(0, 0, 0, 0.7)), url(/wallpaper.jpg)',
                backgroundSize: 'cover',
                backgroundPosition: 'center',
                backgroundAttachment: 'fixed',
                backgroundRepeat: 'no-repeat'
            }}
        >
            <Toaster options={{ fill: '#1a1a1a' }} />
            <BackgroundAnimation />

            <div className="w-full max-w-4xl px-4 z-10">
                <div className="glass-card rounded-2xl p-8 md:p-12 shadow-2xl relative">

                    <div className="text-center mb-8">
                        <h1 className="text-4xl md:text-5xl font-bold title-animate mb-2 pb-4 pt-2 leading-relaxed" style={{ fontFamily: 'var(--font-title)' }}>
                            Manga Downloader
                        </h1>
                        <p className="text-gray-400 text-sm md:text-base">Sistema avanzado de descargas a PDF</p>
                    </div>

                    {/* URL Input */}
                    <div className="flex bg-black/60 rounded-xl overflow-hidden shadow-inner mb-6 border border-white/5 animate-fade-in transition-all focus-within:ring-2 focus-within:ring-blue-500/50">
                        <span className="flex items-center justify-center px-4 bg-white/5 text-gray-300">
                            🔗
                        </span>
                        <input
                            type="text"
                            className="w-full py-4 px-3 bg-transparent text-gray-100 placeholder-gray-500 outline-none"
                            style={{ fontFamily: 'var(--font-input)', letterSpacing: '1px' }}
                            placeholder="Pegar URL aquí (TMO, M440, H2R...)"
                            value={url}
                            onChange={(e) => setUrl(e.target.value)}
                            disabled={false}
                        />
                    </div>

                    {/* Buttons */}
                    <div className="flex justify-end mb-8 animate-fade-in" style={{ animationDelay: '0.1s' }}>
                        <button
                            className="px-8 py-3 rounded-xl bg-[#007bff]/20 text-[#007bff] hover:bg-[#007bff] hover:text-white border border-[#007bff]/50 shadow-[0_0_15px_rgba(0,123,255,0.4)] hover:shadow-[0_0_30px_rgba(0,123,255,0.7)] flex items-center justify-center transition-all hover:-translate-y-1 font-bold disabled:opacity-40 disabled:hover:shadow-none disabled:hover:translate-y-0 disabled:hover:bg-[#007bff]/20 disabled:hover:text-[#007bff] disabled:cursor-not-allowed"
                            style={{ fontFamily: 'var(--font-title)' }}
                            onClick={handleStart}
                            disabled={!url.trim()}
                        >
                            <>
                                <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 mr-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                                </svg>
                                Añadir a la Cola
                            </>
                        </button>
                    </div>

                    {/* Queue Rendering */}
                    <div className="flex flex-col gap-4">
                        {queue.map((item, idx) => (
                            <div key={item.id} className="bg-white/5 border border-white/10 rounded-2xl p-6 transition-all hover:bg-white/10 relative overflow-hidden animate-fade-in" style={{ animationDelay: `${idx * 0.1}s` }}>
                                <div className="flex justify-between items-start mb-4">
                                    <div className="flex-1 mr-4">
                                        <h3 className="text-gray-200 text-sm font-medium truncate w-full" title={item.url}>{item.url}</h3>
                                        <div className="flex items-center mt-2 gap-2">
                                            <span className={`text-xs font-bold px-3 py-1 rounded-full flex items-center gap-1 ${
                                                item.status === 'running' ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30' : 
                                                item.status === 'completed' ? 'bg-green-500/20 text-green-400 border border-green-500/30' : 
                                                item.status === 'error' ? 'bg-red-500/20 text-red-400 border border-red-500/30' : 
                                                item.status === 'cancelled' ? 'bg-orange-500/20 text-orange-400 border border-orange-500/30' : 
                                                'bg-gray-500/20 text-gray-400 border border-gray-500/30'
                                            }`}>
                                                {item.status === 'running' && <span className="w-2 h-2 rounded-full bg-blue-400 animate-pulse"></span>}
                                                {item.status.toUpperCase()}
                                            </span>
                                            {item.status === 'running' && (
                                                <span className="text-xs text-blue-300/80 font-mono">{item.progress}% ({item.current}/{item.total})</span>
                                            )}
                                        </div>
                                    </div>
                                    
                                    {(item.status === 'running' || item.status === 'pending') && (
                                        <button 
                                            onClick={() => handleCancel(item.id)}
                                            className="px-3 py-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/30 text-red-400 border border-red-500/20 transition-colors text-xs font-medium"
                                        >
                                            Cancelar
                                        </button>
                                    )}
                                    {item.status === 'completed' && item.filename && (
                                        <a 
                                            href={`/pdfs/${item.filename}`}
                                            download
                                            className="px-4 py-2 rounded-lg bg-green-500/20 hover:bg-green-500/40 text-green-400 border border-green-500/30 transition-colors text-xs font-bold flex items-center shadow-[0_0_10px_rgba(34,197,94,0.2)] hover:shadow-[0_0_15px_rgba(34,197,94,0.4)]"
                                        >
                                            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                                            </svg>
                                            Descargar
                                        </a>
                                    )}
                                </div>

                                {item.status === 'running' && (
                                    <div className="w-full bg-black/50 rounded-full h-2 mb-4 border border-white/5 overflow-hidden">
                                        <div 
                                            className="bg-gradient-to-r from-blue-500 to-cyan-400 h-full rounded-full transition-all duration-300 relative" 
                                            style={{ width: `${item.progress}%` }}
                                        >
                                            <div className="absolute top-0 left-0 bottom-0 right-0 bg-white/20 animate-pulse"></div>
                                        </div>
                                    </div>
                                )}

                                {item.logs && item.logs.length > 0 && (
                                    <div 
                                        className="text-xs text-green-400/80 h-24 overflow-y-auto bg-black/60 p-3 rounded-xl border border-white/5 font-mono shadow-inner"
                                        style={{ scrollbarWidth: 'thin', scrollbarColor: '#333 transparent' }}
                                        ref={el => { if (el) el.scrollTop = el.scrollHeight; }}
                                    >
                                        {item.logs.slice(-20).map((l, i) => (
                                            <div key={i} className="mb-1 opacity-80 hover:opacity-100 transition-opacity">
                                                <span className="text-gray-500 mr-2 text-[10px]">&gt;</span>{l}
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        ))}
                        {queue.length === 0 && (
                            <div className="text-center py-10 border border-white/5 border-dashed rounded-2xl bg-white/5">
                                <p className="text-gray-400">La cola está vacía. Añade un enlace arriba.</p>
                            </div>
                        )}
                    </div>

                </div>

                <div className="text-center mt-8 text-gray-500 animate-fade-in opacity-80" style={{ animationDelay: '0.3s' }}>
                    <p className="tracking-widest font-bold mb-1" style={{ fontFamily: 'var(--font-signature)', fontSize: '1.2rem' }}>
                        vibecoded by xWolz
                    </p>
                    <small className="text-xs">&copy; 2026 All rights reserved.</small>
                </div>
            </div>
        </main>
    );
}
