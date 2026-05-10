"use client";

import { motion } from 'framer-motion';
import { Loader2, CheckCircle2, XCircle } from 'lucide-react';

export const AnimatedSpinner = () => (
    <motion.div
        animate={{ rotate: 360 }}
        transition={{ repeat: Infinity, duration: 1, ease: "linear" }}
        className="inline-block"
    >
        <Loader2 className="w-5 h-5 text-current" />
    </motion.div>
);

export const AnimatedSuccess = () => (
    <motion.div
        initial={{ scale: 0, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{
            type: "spring",
            stiffness: 260,
            damping: 20
        }}
        className="flex items-center justify-center text-green-500 mb-3"
    >
        <motion.div
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{ duration: 0.5, delay: 0.2 }}
        >
            <CheckCircle2 className="w-16 h-16 drop-shadow-[0_0_15px_rgba(34,197,94,0.5)]" />
        </motion.div>
    </motion.div>
);

export const AnimatedError = () => (
    <motion.div
        initial={{ scale: 0, opacity: 0, rotate: -180 }}
        animate={{ scale: 1, opacity: 1, rotate: 0 }}
        transition={{
            type: "spring",
            stiffness: 260,
            damping: 20
        }}
        className="flex items-center justify-center text-red-500 mb-3"
    >
        <XCircle className="w-16 h-16 drop-shadow-[0_0_15px_rgba(239,68,68,0.5)]" />
    </motion.div>
);
