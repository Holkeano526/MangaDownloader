"use client";

import { useEffect, useRef } from 'react';

const BackgroundAnimation = () => {
    const canvasRef = useRef(null);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        let animationFrameId;

        // Configuration
        const particleCount = 1200; // 200% más puntos (400 -> 1200)
        const dotSize = 1;         // Más pequeños para elegancia
        const mouseRadius = 300;   // Radio más amplio
        const forceFactor = 10;    // Fuerza de empuje ajustada
        const returnSpeed = 0.04;  // Retorno fluido
        const dotColor = 'rgba(255, 255, 255, 0.7)'; // White, semi-transparente

        let width, height;
        let dots = [];
        let mouse = { x: -1000, y: -1000 };

        const resize = () => {
            width = window.innerWidth;
            height = window.innerHeight;
            canvas.width = width;
            canvas.height = height;
            initDots();
        };

        const initDots = () => {
            dots = [];
            for (let i = 0; i < particleCount; i++) {
                const x = Math.random() * width;
                const y = Math.random() * height;
                dots.push({
                    x: x,
                    y: y,
                    originX: x,
                    originY: y,
                    vx: 0,
                    vy: 0,
                    size: Math.random() * 1.5 + 0.5,
                    friction: Math.random() * 0.05 + 0.90
                });
            }
        };

        const animate = () => {
            ctx.clearRect(0, 0, width, height);
            ctx.fillStyle = dotColor;

            dots.forEach(dot => {
                const dx = mouse.x - dot.x;
                const dy = mouse.y - dot.y;
                const distance = Math.sqrt(dx * dx + dy * dy);

                if (distance < mouseRadius) {
                    const angle = Math.atan2(dy, dx);
                    const force = (mouseRadius - distance) / mouseRadius;

                    const power = Math.pow(force, 3);

                    const moveX = Math.cos(angle) * power * forceFactor;
                    const moveY = Math.sin(angle) * power * forceFactor;

                    dot.vx -= moveX;
                    dot.vy -= moveY;
                }

                const dxHome = dot.originX - dot.x;
                const dyHome = dot.originY - dot.y;

                dot.vx += dxHome * returnSpeed;
                dot.vy += dyHome * returnSpeed;

                dot.vx *= dot.friction;
                dot.vy *= dot.friction;

                dot.x += dot.vx;
                dot.y += dot.vy;

                ctx.beginPath();
                ctx.arc(dot.x, dot.y, dot.size, 0, Math.PI * 2);
                ctx.fill();
            });

            animationFrameId = requestAnimationFrame(animate);
        };

        const handleMouseMove = (e) => {
            mouse.x = e.clientX;
            mouse.y = e.clientY;
        };

        const handleMouseLeave = () => {
            mouse.x = -1000;
            mouse.y = -1000;
        };

        window.addEventListener('resize', resize);
        window.addEventListener('mousemove', handleMouseMove);
        window.addEventListener('mouseout', handleMouseLeave);

        resize();
        animate();

        return () => {
            window.removeEventListener('resize', resize);
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('mouseout', handleMouseLeave);
            cancelAnimationFrame(animationFrameId);
        };
    }, []);

    return (
        <canvas
            ref={canvasRef}
            className="fixed top-0 left-0 w-screen h-screen z-0 pointer-events-none"
        />
    );
};

export default BackgroundAnimation;
