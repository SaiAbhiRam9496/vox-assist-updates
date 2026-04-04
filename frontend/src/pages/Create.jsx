import React, { useState, useRef, Suspense, useEffect } from 'react';
import { Canvas, useLoader, useThree } from '@react-three/fiber';
import { OrbitControls, Center, Grid, Html, GizmoHelper } from '@react-three/drei';
import { PLYLoader } from 'three/examples/jsm/loaders/PLYLoader';
import { STLExporter } from 'three/examples/jsm/exporters/STLExporter';
import { motion, AnimatePresence } from 'framer-motion';
import { useAuth } from '../contexts/AuthContext';
import axios from 'axios';
import { Loader2, Send, Download, Plus, Trash2, ArrowRight, ArrowLeft, CheckCircle2, Printer, Box, Link2 } from 'lucide-react';
import * as THREE from 'three';
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from 'recharts';

// Grouped room options — rendered as <optgroup> sections in the dropdown.
// Hallway is intentionally EXCLUDED — it is auto-injected by the engine.
// Users never need to choose "Hallway" explicitly.
const ROOM_OPTION_GROUPS = [
    {
        label: 'Living Spaces',
        options: [
            { value: 'living', label: 'Living Room' },
            { value: 'dining', label: 'Dining Room' },
        ]
    },
    {
        label: 'Bedrooms',
        options: [
            { value: 'bedroom', label: 'Bedroom' },
            { value: 'study', label: 'Study / Office' },
        ]
    },
    {
        label: 'Utilities',
        options: [
            { value: 'kitchen', label: 'Kitchen' },
            { value: 'bathroom', label: 'Bathroom' },
            { value: 'storage', label: 'Storage / Closet' },
        ]
    },
    {
        label: 'Other',
        options: [
            { value: 'balcony', label: 'Balcony / Terrace' },
        ]
    },
];

// Flat list — used for simple value→label lookups
const ROOM_OPTIONS = ROOM_OPTION_GROUPS.flatMap(g => g.options);

function convertPromptUnits(text, fromUnit, toUnit) {
    if (!text || fromUnit === toUnit) return text;
    const factor = fromUnit === 'ft' ? 0.092903 : 10.7639;
    const fromRegex =
        fromUnit === 'ft'
            ? /(\d+(?:\.\d+)?)\s*(?:sqft|sq\s*ft|square\s+feet)/gi
            : /(\d+(?:\.\d+)?)\s*(?:sqm|sq\s*m|square\s+meters?)/gi;
    const toLabel = toUnit === 'ft' ? 'sqft' : 'sqm';
    return text.replace(fromRegex, (_match, num) => {
        const converted = Math.round(parseFloat(num) * factor);
        return `${converted} ${toLabel}`;
    });
}

// Bridge component to expose Three.js scene and renderer to parent via refs
const SceneExporter = ({ sceneRef, glRef }) => {
    const { scene, gl } = useThree();
    useEffect(() => {
        sceneRef.current = scene;
        if (glRef) glRef.current = gl;
    }, [scene, gl, sceneRef, glRef]);
    return null;
};

const Model = ({ url }) => {
    const geometry = useLoader(PLYLoader, url);
    const { camera, controls } = useThree();

    useEffect(() => {
        if (geometry) {
            geometry.computeVertexNormals();
            geometry.computeBoundingBox();

            const box = geometry.boundingBox;
            const center = new THREE.Vector3();
            box.getCenter(center);

            const size = new THREE.Vector3();
            box.getSize(size);

            const maxDim = Math.max(size.x, size.y, size.z);
            const fov = camera.fov * (Math.PI / 180);
            let cameraDist = Math.abs(maxDim / 2 / Math.tan(fov / 2)) * 2.0;

            const newPos = new THREE.Vector3(center.x + cameraDist, center.y + cameraDist, center.z + cameraDist);
            camera.position.copy(newPos);
            camera.lookAt(center);

            if (controls) {
                controls.target.copy(center);
                controls.update();
            }
        }
    }, [geometry, url, camera, controls]);

    return (
        <group>
            <mesh geometry={geometry}>
                <meshStandardMaterial vertexColors={true} side={THREE.DoubleSide} roughness={0.8} />
            </mesh>
        </group>
    );
};

const RoomHighlight = ({ roomPoly, color }) => {
    if (!roomPoly) return null;
    const shape = new THREE.Shape();
    let coords = roomPoly.coordinates;
    if (Array.isArray(coords[0]) && Array.isArray(coords[0][0])) coords = coords[0];

    if (coords && coords.length > 0) {
        shape.moveTo(coords[0][0], coords[0][1]);
        for (let i = 1; i < coords.length; i++) shape.lineTo(coords[i][0], coords[i][1]);
    }

    return (
        <mesh position={[0, 0, 0.1]}>
            <extrudeGeometry args={[shape, { depth: 2.8, bevelEnabled: false }]} />
            <meshBasicMaterial color={color || "#ff00ff"} transparent opacity={0.6} side={THREE.DoubleSide} depthTest={false} />
        </mesh>
    );
};

const InteractiveRoom = ({ roomPoly, roomId, setHoveredRoomId, isHovered, roomSpec, unit, computeRoomDimensions }) => {
    const [hoverPoint, setHoverPoint] = useState(null);
    if (!roomPoly) return null;
    const shape = new THREE.Shape();
    let coords = roomPoly.coordinates;
    if (Array.isArray(coords[0]) && Array.isArray(coords[0][0])) coords = coords[0];

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    if (coords && coords.length > 0) {
        shape.moveTo(coords[0][0], coords[0][1]);
        for (let i = 1; i < coords.length; i++) {
            shape.lineTo(coords[i][0], coords[i][1]);
            const [px, py] = coords[i];
            if (px < minX) minX = px;
            if (px > maxX) maxX = px;
            if (py < minY) minY = py;
            if (py > maxY) maxY = py;
        }
    }

    const cx = minX !== Infinity ? (minX + maxX) / 2 : 0;
    const cy = minY !== Infinity ? (minY + maxY) / 2 : 0;
    const dims = computeRoomDimensions ? computeRoomDimensions(roomId) : null;

    return (
        <mesh
            position={[0, 0, 0]}
            onPointerOver={(e) => { e.stopPropagation(); setHoveredRoomId(roomId); }}
            onPointerOut={() => { setHoveredRoomId(null); setHoverPoint(null); }}
            onPointerMove={(e) => { e.stopPropagation(); setHoverPoint(e.point); }}
        >
            <extrudeGeometry args={[shape, { depth: 2.8, bevelEnabled: false }]} />
            <meshBasicMaterial transparent opacity={0.0} depthTest={false} />
            {isHovered && roomSpec && dims && (
                <Html position={hoverPoint ? [hoverPoint.x, hoverPoint.y, 3] : [cx, cy, 3]} center style={{ pointerEvents: 'none', zIndex: 100 }}>
                    <div className="bg-white/95 backdrop-blur-sm p-3 rounded-lg shadow-xl border border-stone-200 text-xs text-stone-700 font-mono w-max -translate-y-16">
                        <p className="font-bold text-charcoal mb-1 flex items-center gap-2">
                            <span className="w-2.5 h-2.5 rounded-full inline-block" style={{ backgroundColor: roomSpec.color || '#e5e7eb' }}></span>
                            <span className="capitalize">{roomSpec.type}</span>
                        </p>
                        <p>{Math.round(roomSpec.area)} sq{unit} <span className="text-stone-400">({unit === 'ft' ? Math.round(roomSpec.area / 10.7639) + ' sqm' : Math.round(roomSpec.area * 10.7639) + ' sqft'})</span></p>
                        <p className="text-[10px] text-stone-500 mt-1 border-t border-stone-100 pt-1">
                            {unit === 'ft' ? `${dims.ft.w} × ${dims.ft.h} ft (${dims.m.w} × ${dims.m.h} m)` : `${dims.m.w} × ${dims.m.h} m (${dims.ft.w} × ${dims.ft.h} ft)`}
                        </p>
                    </div>
                </Html>
            )}
        </mesh>
    );
};

const Create = () => {
    const { currentUser } = useAuth();

    // Wizard State
    const [step, setStep] = useState(1); // 1: Rooms, 2: Style, 3: Review, 4: Generating, 5: Results
    const [rooms, setRooms] = useState([
        { id: 1, type: 'living', area: 300 },
        { id: 2, type: 'bedroom', area: 150 },
        { id: 3, type: 'bathroom', area: 50 },
        { id: 4, type: 'kitchen', area: 120 }
    ]);
    const [inputMode, setInputMode] = useState('manual'); // 'manual' or 'text'
    const [unit, setUnit] = useState('ft'); // 'ft' or 'm'
    const [gridSize, setGridSize] = useState(1.0);
    const [textPrompt, setTextPrompt] = useState('');
    const [totalAreaConstraint, setTotalAreaConstraint] = useState(1000);
    const [validationError, setValidationError] = useState('');

    const [adjacencyPairs, setAdjacencyPairs] = useState([]);

    // Generation State
    const [loading, setLoading] = useState(false);
    const [generationStatus, setGenerationStatus] = useState(''); // Simulated SSE
    const [modelUrl, setModelUrl] = useState(null);
    const [layoutSpec, setLayoutSpec] = useState(null);
    const [layoutData, setLayoutData] = useState(null);
    const [score, setScore] = useState(0);
    const [stats, setStats] = useState(null);
    const [error, setError] = useState(null);
    const [hoveredRoomId, setHoveredRoomId] = useState(null);
    const [candidates, setCandidates] = useState([]);
    const [selectedCandidateId, setSelectedCandidateId] = useState(null);
    const sceneRef = useRef();
    const glRef = useRef();
    const fullLayoutRef = useRef(null);

    // --- EXPORT HANDLERS ---
    const handleExportSTL = () => {
        if (!sceneRef.current) return;
        const exporter = new STLExporter();
        const stlString = exporter.parse(sceneRef.current);
        const blob = new Blob([stlString], { type: 'text/plain' });
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = 'voxassist_layout.stl';
        link.click();
        URL.revokeObjectURL(link.href);
    };

    const handleDownloadBlueprint = async () => {
        if (!glRef.current || !layoutSpec) return;
        try {
            // Capture 3D Canvas screenshot
            const renderer = glRef.current;
            const screenshot = renderer.domElement.toDataURL('image/png');

            // Build room summary for the PDF
            const roomSummary = layoutSpec.rooms.map(r => ({
                name: r.type,
                area_sqft: Math.round(r.area),
                area_sqm: Math.round(r.area / 10.7639),
                color: r.color
            }));

            const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
            const token = await currentUser?.getIdToken();
            const res = await axios.post(
                `${API_URL}/api/v1/blueprint`,
                {
                    layout_data: fullLayoutRef.current,
                    screenshot_base64: screenshot,
                    room_summary: roomSummary,
                    score: score,
                    prompt: compiledPrompt
                },
                {
                    headers: { Authorization: `Bearer ${token}` },
                    responseType: 'blob'
                }
            );
            const url = window.URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }));
            const link = document.createElement('a');
            link.href = url;
            link.download = 'voxassist_blueprint.pdf';
            link.click();
            window.URL.revokeObjectURL(url);
        } catch (err) {
            console.error('Blueprint download failed:', err);
        }
    };

    // Derived compiled prompt
    const compiledPrompt = inputMode === 'text'
        ? textPrompt
        : `A house with a total area of around ${totalAreaConstraint} sq${unit}. It includes: ` +
        rooms.map(r => `a ${r.area} sq${unit} ${r.type} room`).join(', ') + '.';

    const compiledAdjacencyPairs = adjacencyPairs
        .filter(p => p.roomA && p.roomB && p.roomA !== p.roomB)
        .map(p => [p.roomA, p.roomB]);

    // Area Distribution Graph Data
    const totalRoomArea = rooms.reduce((acc, r) => acc + parseInt(r.area || 0), 0);
    const unusedArea = Math.max(0, totalAreaConstraint - totalRoomArea);
    const pieData = rooms.map((r, i) => ({
        name: r.type,
        value: parseInt(r.area || 0),
        color: ['#10b981', '#6366f1', '#f59e0b', '#ec4899', '#8b5cf6', '#ef4444'][i % 6]
    }));
    if (unusedArea > 0) pieData.push({ name: 'Unallocated', value: unusedArea, color: '#f5f5f4' });

    const handleAddRoom = () => {
        setRooms([...rooms, { id: Date.now(), type: 'bedroom', area: 100 }]);
    };

    const handleRoomChange = (id, field, value) => {
        setRooms(rooms.map(r => r.id === id ? { ...r, [field]: value } : r));
    };

    const handleRemoveRoom = (id) => {
        setRooms(rooms.filter(r => r.id !== id));
    };

    // ── Dynamic room instances for adjacency dropdowns ───────────────────────
    // Built from the current `rooms` state in manual mode. Each entry has a
    // stable `instanceKey` (e.g. "bedroom_1") and a human label ("Bedroom 1").
    // When there's only one room of a type, no number is appended.
    const roomInstances = (() => {
        const typeTotals = {};
        rooms.forEach(r => { typeTotals[r.type] = (typeTotals[r.type] || 0) + 1; });
        const counters = {};
        return rooms.map(r => {
            counters[r.type] = (counters[r.type] || 0) + 1;
            const instance = counters[r.type];
            const multi = typeTotals[r.type] > 1;
            const typeLabel = ROOM_OPTIONS.find(o => o.value === r.type)?.label || r.type;
            return {
                key:   multi ? `${r.type}_${instance}` : r.type,
                label: multi ? `${typeLabel} ${instance}` : typeLabel,
                type:  r.type,
            };
        });
    })();

    const handleAddAdjacency = () => {
        // Default to the first two distinct instances, or fallback values
        const first  = roomInstances[0]?.key  || 'bedroom';
        const second = roomInstances.find(r => r.key !== first)?.key || 'living';
        setAdjacencyPairs([...adjacencyPairs, { id: Date.now(), roomA: first, roomB: second }]);
    };

    const handleUpdateAdjacency = (id, field, value) => {
        setAdjacencyPairs(adjacencyPairs.map(p => p.id === id ? { ...p, [field]: value } : p));
    };

    const handleRemoveAdjacency = (id) => {
        setAdjacencyPairs(adjacencyPairs.filter(p => p.id !== id));
    };

    const handleUnitToggle = () => {
        const fromUnit = unit;
        const toUnit = unit === 'ft' ? 'm' : 'ft';
        setUnit(toUnit);
        if (inputMode === 'text' && textPrompt.trim()) {
            setTextPrompt(prev => convertPromptUnits(prev, fromUnit, toUnit));
        }
    };

    const computeRoomDimensions = (roomId) => {
        const poly = layoutData?.[roomId];
        if (!poly) return null;
        let coords = poly.coordinates;
        if (Array.isArray(coords[0]) && Array.isArray(coords[0][0])) coords = coords[0];
        if (!coords || coords.length === 0) return null;

        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        for (let p of coords) {
            if (p[0] < minX) minX = p[0];
            if (p[0] > maxX) maxX = p[0];
            if (p[1] < minY) minY = p[1];
            if (p[1] > maxY) maxY = p[1];
        }

        let rawW = Math.abs(maxX - minX);
        let rawH = Math.abs(maxY - minY);

        return {
            m: { w: rawW.toFixed(1), h: rawH.toFixed(1) },
            ft: { w: (rawW * 3.28084).toFixed(1), h: (rawH * 3.28084).toFixed(1) }
        };
    };

    const CustomTooltip = ({ active, payload }) => {
        if (active && payload && payload.length) {
            const data = payload[0].payload;
            const dims = data.id ? computeRoomDimensions(data.id) : null;
            return (
                <div className="bg-white/95 backdrop-blur-sm p-3 rounded-lg shadow-xl border border-stone-200 text-xs text-stone-700 font-mono z-50 pointer-events-none">
                    <p className="font-bold text-charcoal mb-1 flex items-center gap-2">
                        <span className="w-2.5 h-2.5 rounded-full inline-block" style={{ backgroundColor: data.color || '#e5e7eb' }}></span>
                        <span className="capitalize">{data.type || data.name}</span>
                    </p>
                    <p>{Math.round(data.area || data.value)} sq{unit} <span className="text-stone-400">({unit === 'ft' ? Math.round((data.area || data.value) / 10.7639) + ' sqm' : Math.round((data.area || data.value) * 10.7639) + ' sqft'})</span></p>
                    {dims && (
                        <p className="text-[10px] text-stone-500 mt-1 border-t border-stone-100 pt-1">
                            {unit === 'ft' ? `${dims.ft.w} × ${dims.ft.h} ft (${dims.m.w} × ${dims.m.h} m)` : `${dims.m.w} × ${dims.m.h} m (${dims.ft.w} × ${dims.ft.h} ft)`}
                        </p>
                    )}
                </div>
            );
        }
        return null;
    };

    const validateStep1 = () => {
        if (inputMode === 'text') {
            if (textPrompt.trim().length < 10) {
                setValidationError('Please provide a more detailed text description of your desired rooms.');
                return false;
            }
        } else {
            const totalRoomArea = rooms.reduce((acc, r) => acc + parseInt(r.area || 0), 0);
            // Soft advisory — only block at 150%+. The backend scales rooms
            // proportionally, so moderate overages are fine.
            if (totalRoomArea > totalAreaConstraint * 1.5) {
                setValidationError(
                    `Note: Rooms total ${totalRoomArea} sq${unit} vs ${totalAreaConstraint} sq${unit} target. ` +
                    `The AI will scale rooms proportionally. Click "Review Design" again to proceed.`
                );
                return false;
            }
        }
        for (const p of adjacencyPairs) {
            if (p.roomA === p.roomB) {
                setValidationError('An adjacency pair cannot reference the same room twice.');
                return false;
            }
        }
        setValidationError('');
        return true;
    };

    const nextStep = () => {
        if (step === 1 && !validateStep1()) return;
        setStep(s => s + 1);
    };

    const prevStep = () => {
        setStep(s => Math.max(1, s - 1));
    };

    const handleGenerate = async () => {
        setStep(3); // Generating step
        setLoading(true);
        setError(null);
        setCandidates([]);
        setSelectedCandidateId(null);
        setModelUrl(null);
        setLayoutSpec(null);
        setLayoutData(null);

        // Simulate Status Stream
        const statuses = ["Analyzing constraints...", "Generating adjacency graphs...", "Optimizing layouts...", "Rendering 3D Models...", "Finalizing Details..."];
        let sIdx = 0;
        const statusInterval = setInterval(() => {
            setGenerationStatus(statuses[sIdx]);
            sIdx = Math.min(sIdx + 1, statuses.length - 1);
        }, 3000);

        try {
            const token = await currentUser.getIdToken();
            
            // Prepare payload
            const payload = { 
                prompt: compiledPrompt, 
                adjacency_pairs: compiledAdjacencyPairs, 
                unit 
            };
            
            // Add rooms_spec in manual mode (bypass NLP parsing).
            // Each room includes instance number and display name so the backend
            // can correctly label Bedroom 1, Bedroom 2 etc.
            if (inputMode === 'manual') {
                // Count instances per type to build numbered names
                const instanceCounters = {};
                const typeTotals = {};
                rooms.forEach(r => { typeTotals[r.type] = (typeTotals[r.type] || 0) + 1; });

                payload.rooms_spec = rooms.map(room => {
                    instanceCounters[room.type] = (instanceCounters[room.type] || 0) + 1;
                    const instance = instanceCounters[room.type];
                    const name = typeTotals[room.type] > 1
                        ? `${room.type}_${instance}`
                        : room.type;
                    return {
                        type: room.type,
                        name,
                        instance,
                        // unit state is 'ft' or 'm' — backend always expects sqft
                        area: unit === 'm' ? room.area * 10.764 : room.area,
                    };
                });
            }
            
            const response = await axios.post(
                `${import.meta.env.VITE_API_URL}/api/v1/generate`,
                payload,
                { headers: { Authorization: `Bearer ${token}` } }
            );

            if (response.data.success) {
                const jobId = response.data.job_id;

                // Poll for completion
                const pollInterval = setInterval(async () => {
                    try {
                        const jobRes = await axios.get(`${import.meta.env.VITE_API_URL}/api/v1/jobs/${jobId}`, {
                            headers: { Authorization: `Bearer ${token}` }
                        });

                        if (jobRes.data.status === 'completed') {
                            clearInterval(pollInterval);
                            clearInterval(statusInterval);
                            const result = jobRes.data.result;
                            const best = result.candidates.find(c => c.model_url === result.model_url) || result.candidates[0];
                            setCandidates(result.candidates);
                            handleSelectCandidate(best);
                            setStep(4); // Results
                        } else if (jobRes.data.status === 'failed') {
                            clearInterval(pollInterval);
                            clearInterval(statusInterval);
                            setError(jobRes.data.error || "Generation failed during processing.");
                            setStep(2); // Go back to review on err
                        }
                    } catch (e) {
                        console.error("Polling error", e);
                    }
                }, 2000);
            } else {
                clearInterval(statusInterval);
                setError(response.data.error || "Failed to start generation");
                setStep(2);
            }
        } catch (err) {
            clearInterval(statusInterval);
            console.error(err);
            setError("Failed to connect to server. Ensure Backend is running.");
            setStep(2);
        } finally {
            setLoading(false);
        }
    };

    const handleSelectCandidate = (candidate) => {
        setSelectedCandidateId(candidate.id);
        setModelUrl(`${import.meta.env.VITE_API_URL}${candidate.model_url}`);
        setLayoutSpec(candidate.spec);
        setLayoutData(candidate.layout.rooms);
        fullLayoutRef.current = candidate.layout;
        setScore(candidate.score);
        setStats(candidate.stats);
    };

    const resetWizard = () => {
        setStep(1);
        setCandidates([]);
        setModelUrl(null);
        fullLayoutRef.current = null;
    };

    const hoveredPoly = hoveredRoomId && layoutData ? layoutData[hoveredRoomId] : null;
    const hoveredColor = hoveredRoomId && layoutSpec ? layoutSpec.rooms.find(r => r.id === hoveredRoomId)?.color : null;

    return (
        <div className="pt-20 px-4 h-screen flex flex-col md:flex-row overflow-hidden bg-cream">
            {/* Left Panel: Wizard Input & Stats */}
            <motion.div
                initial={{ x: -20, opacity: 0 }}
                animate={{ x: 0, opacity: 1 }}
                className="w-full md:w-1/3 p-6 flex flex-col bg-white border-r border-stone-200 z-10 shadow-lg md:h-full overflow-y-auto"
            >
                {step < 3 && (
                    <div className="mb-8">
                        <div className="flex items-center justify-between mb-2">
                            <h1 className="text-2xl font-light text-charcoal">Design Wizard</h1>
                            <span className="text-sm font-medium text-stone-500">Step {step} of 2</span>
                        </div>
                        <div className="h-1 w-full bg-stone-100 rounded-full overflow-hidden">
                            <motion.div className="h-full bg-charcoal" initial={{ width: 0 }} animate={{ width: `${(step / 2) * 100}%` }} />
                        </div>
                    </div>
                )}

                <AnimatePresence mode="wait">
                    {/* STEP 1: Rooms */}
                    {step === 1 && (
                        <motion.div key="step1" initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 10 }} className="flex-1">
                            <h2 className="text-lg font-medium text-stone-800 mb-4">1. Room Requirements</h2>

                            <div className="flex bg-stone-100 p-1 rounded-xl mb-6">
                                <button
                                    onClick={() => setInputMode('manual')}
                                    className={`flex-1 py-1.5 text-sm font-medium rounded-lg transition-all ${inputMode === 'manual' ? 'bg-white shadow border border-stone-200 text-charcoal' : 'text-stone-500 hover:text-stone-700'}`}
                                >
                                    Room Builder
                                </button>
                                <button
                                    onClick={() => setInputMode('text')}
                                    className={`flex-1 py-1.5 text-sm font-medium rounded-lg transition-all ${inputMode === 'text' ? 'bg-white shadow border border-stone-200 text-charcoal' : 'text-stone-500 hover:text-stone-700'}`}
                                >
                                    Text Prompt
                                </button>
                            </div>

                            {inputMode === 'manual' ? (
                                <>
                                    <div className="mb-6 bg-stone-50 p-4 rounded-xl border border-stone-200">
                                        <div className="flex justify-between items-center mb-1">
                                            <label className="block text-sm font-medium text-stone-700">Target Total Area (sq{unit})</label>
                                            <button
                                                onClick={handleUnitToggle}
                                                className="text-xs bg-stone-200 text-stone-600 px-2 py-0.5 rounded font-mono hover:bg-stone-300"
                                            >
                                                {unit === 'ft' ? 'Switch to Meters' : 'Switch to Feet'}
                                            </button>
                                        </div>
                                        <input
                                            type="number"
                                            value={totalAreaConstraint}
                                            onChange={(e) => setTotalAreaConstraint(Number(e.target.value))}
                                            className="w-full p-2 border border-stone-200 rounded-lg outline-none"
                                        />
                                    </div>

                                    <div className="mb-6 h-56 bg-stone-50 rounded-xl border border-stone-200 p-4 flex flex-col relative w-full items-center justify-center">
                                        <div className="absolute top-2 left-3 text-xs font-bold text-stone-400 tracking-wider z-10">AREA DISTRIBUTION</div>
                                        {/* Center Label (Placed before chart to fix z-index tooltip overlap) */}
                                        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none pt-4 z-0">
                                            <span className="text-stone-400 text-[10px] font-bold">TOTAL</span>
                                            <span className="text-stone-700 text-sm font-bold">{totalAreaConstraint}</span>
                                        </div>
                                        <div className="w-full h-full pt-4 relative z-10">
                                            <ResponsiveContainer width="100%" height="100%">
                                                <PieChart>
                                                    <Pie
                                                        data={pieData}
                                                        cx="50%"
                                                        cy="50%"
                                                        innerRadius={45}
                                                        outerRadius={70}
                                                        paddingAngle={2}
                                                        dataKey="value"
                                                        stroke="none"
                                                    >
                                                        {pieData.map((entry, index) => (
                                                            <Cell key={`cell-${index}`} fill={entry.color} />
                                                        ))}
                                                    </Pie>
                                                    <Tooltip content={<CustomTooltip />} />
                                                </PieChart>
                                            </ResponsiveContainer>
                                        </div>
                                    </div>

                                    <div className="space-y-3 mb-6">
                                        {rooms.map((room, idx) => (
                                            <div key={room.id} className="flex gap-2 items-center">
                                                <div className="w-8 text-center text-xs font-medium text-stone-400">{idx + 1}</div>
                                                <select
                                                    value={room.type}
                                                    onChange={(e) => handleRoomChange(room.id, 'type', e.target.value)}
                                                    className="flex-1 p-2 border border-stone-200 rounded-lg outline-none text-sm bg-white"
                                                >
                                                    {ROOM_OPTION_GROUPS.map(group => (
                                                        <optgroup key={group.label} label={group.label}>
                                                            {group.options.map(o => (
                                                                <option key={o.value} value={o.value}>{o.label}</option>
                                                            ))}
                                                        </optgroup>
                                                    ))}
                                                </select>
                                                <input
                                                    type="number"
                                                    value={room.area}
                                                    onChange={(e) => handleRoomChange(room.id, 'area', Number(e.target.value))}
                                                    className="w-24 p-2 border border-stone-200 rounded-lg outline-none text-sm text-center"
                                                    placeholder={`sq${unit}`}
                                                />
                                                <button onClick={() => handleRemoveRoom(room.id)} className="p-2 text-stone-400 hover:text-red-500">
                                                    <Trash2 size={16} />
                                                </button>
                                            </div>
                                        ))}
                                        <button onClick={handleAddRoom} className="w-full py-2 border-2 border-dashed border-stone-200 text-stone-500 rounded-lg text-sm hover:border-stone-400 hover:text-stone-700 flex items-center justify-center gap-1">
                                            <Plus size={16} /> Add Room
                                        </button>
                                    </div>
                                    <div className="mb-6 pt-4 border-t border-stone-100">
                                        <div className="flex justify-between items-center mb-2">
                                            <div>
                                                <label className="block text-sm font-medium text-stone-700">Adjacency Constraints</label>
                                                <p className="text-[11px] text-stone-400 mt-0.5">Prefer these rooms to share a wall</p>
                                            </div>
                                            <button
                                                onClick={handleAddAdjacency}
                                                className="text-xs text-charcoal border border-stone-200 px-2 py-1 rounded-lg hover:bg-stone-50 flex items-center gap-1"
                                            >
                                                <Plus size={12} /> Add
                                            </button>
                                        </div>

                                        {adjacencyPairs.length === 0 && (
                                            <p className="text-xs text-stone-400 italic py-2">
                                                No constraints — rooms will be placed automatically.
                                            </p>
                                        )}

                                        {roomInstances.length < 2 && adjacencyPairs.length > 0 && (
                                            <p className="text-xs text-amber-500 italic py-1">
                                                Add at least 2 rooms above to set adjacency constraints.
                                            </p>
                                        )}

                                        <div className="space-y-2">
                                            {adjacencyPairs.map((pair) => (
                                                <div key={pair.id} className="flex gap-2 items-center bg-stone-50 p-2 rounded-lg border border-stone-100">
                                                    <select
                                                        value={pair.roomA}
                                                        onChange={(e) => handleUpdateAdjacency(pair.id, 'roomA', e.target.value)}
                                                        className="flex-1 p-1.5 border border-stone-200 rounded-lg text-xs bg-white outline-none"
                                                    >
                                                        {roomInstances.map(inst => (
                                                            <option key={inst.key} value={inst.key}>{inst.label}</option>
                                                        ))}
                                                    </select>

                                                    <div className="flex items-center gap-0.5 text-[10px] text-stone-400 font-bold">
                                                        <Link2 size={12} className="text-charcoal/40" />
                                                    </div>

                                                    <select
                                                        value={pair.roomB}
                                                        onChange={(e) => handleUpdateAdjacency(pair.id, 'roomB', e.target.value)}
                                                        className="flex-1 p-1.5 border border-stone-200 rounded-lg text-xs bg-white outline-none"
                                                    >
                                                        {roomInstances
                                                            .filter(inst => inst.key !== pair.roomA)
                                                            .map(inst => (
                                                                <option key={inst.key} value={inst.key}>{inst.label}</option>
                                                            ))
                                                        }
                                                    </select>

                                                    <button
                                                        onClick={() => handleRemoveAdjacency(pair.id)}
                                                        className="p-1 text-stone-400 hover:text-red-500 flex-shrink-0"
                                                    >
                                                        <Trash2 size={14} />
                                                    </button>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                </>
                            ) : (
                                <div className="mb-6">
                                    <div className="flex justify-between items-end mb-2">
                                        <p className="text-sm text-stone-500">Describe the rooms and dimensions you want conversationally.</p>
                                        <button
                                            onClick={handleUnitToggle}
                                            className="text-xs bg-stone-200 text-stone-600 px-2 py-0.5 rounded font-mono hover:bg-stone-300 whitespace-nowrap"
                                        >
                                            {unit === 'ft' ? 'Switch to Meters' : 'Switch to Feet'}
                                        </button>
                                    </div>
                                    <textarea
                                        value={textPrompt}
                                        onChange={(e) => setTextPrompt(e.target.value)}
                                        placeholder={`E.g., I want a 1000 sq${unit} house with a 300 sq${unit} living room, a 150 sq${unit} bedroom...`}
                                        className="w-full p-4 rounded-xl border border-stone-200 focus:border-charcoal focus:ring-1 focus:ring-charcoal outline-none resize-none h-48 text-stone-700 bg-stone-50"
                                    />
                                    <p className="text-[11px] text-stone-400 mt-1">Tip: you can type adjacency hints like "Bedroom adjacent to Study".</p>
                                </div>
                            )}

                            {validationError && (
                                <div className="p-3 bg-red-50 text-red-600 text-sm rounded-lg mb-6 border border-red-100 flex items-start gap-2">
                                    <span className="mt-0.5">⚠️</span> {validationError}
                                </div>
                            )}

                            <div className="flex justify-end">
                                <button onClick={nextStep} className="px-6 py-2.5 bg-charcoal text-white rounded-xl hover:bg-stone-800 flex items-center gap-2">
                                    Review Design <ArrowRight size={16} />
                                </button>
                            </div>
                        </motion.div>
                    )}

                    {/* STEP 2: Review */}
                    {step === 2 && (
                        <motion.div key="step2" initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 10 }} className="flex-1 flex flex-col">
                            <h2 className="text-lg font-medium text-stone-800 mb-4">2. Review Details</h2>

                            <div className="flex-1 bg-stone-50 rounded-xl p-5 border border-stone-200 mb-6 font-mono text-sm text-stone-600 leading-relaxed overflow-y-auto">
                                <div className="mb-4">
                                    <span className="font-bold text-stone-800">Prompt sent to AI:</span><br />
                                    {compiledPrompt}
                                </div>
                                <div>
                                    <span className="font-bold text-stone-800">Metrics:</span><br />
                                    {inputMode === 'manual' ? (
                                        <>
                                            - Total Target Area: {totalAreaConstraint} sq{unit}<br />
                                            - Room Count: {rooms.length}
                                        </>
                                    ) : (
                                        <span>- Text Prompt Driven Mode</span>
                                    )}
                                </div>
                            </div>

                            {error && (
                                <div className="p-3 bg-red-50 text-red-600 text-sm rounded-lg mb-6 border border-red-100">
                                    {error}
                                </div>
                            )}

                            <div className="flex justify-between mt-auto">
                                <button onClick={prevStep} className="px-6 py-2.5 text-stone-600 border border-stone-200 rounded-xl hover:bg-stone-50 flex items-center gap-2">
                                    <ArrowLeft size={16} /> Edit
                                </button>
                                <button onClick={handleGenerate} className="px-6 py-2.5 bg-charcoal text-white rounded-xl hover:bg-stone-800 flex items-center gap-2 shadow-lg">
                                    <Send size={16} /> Generate Now
                                </button>
                            </div>
                        </motion.div>
                    )}

                    {/* STEP 3: Generating Overlay UI on left side */}
                    {step === 3 && (
                        <motion.div key="step3" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex-1 flex flex-col items-center justify-center text-center">
                            <Loader2 className="w-12 h-12 animate-spin text-charcoal mb-6" />
                            <h2 className="text-xl font-light text-charcoal mb-2">Architecting Solutions</h2>
                            <p className="text-stone-500 font-mono text-xs bg-stone-100 px-4 py-2 rounded-full">{generationStatus || "Initializing..."}</p>
                        </motion.div>
                    )}

                    {/* STEP 4: Results */}
                    {step === 4 && layoutSpec && stats && (
                        <motion.div key="step4" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-col h-full pl-1">
                            <div className="flex justify-between items-center mb-6">
                                <h1 className="text-2xl font-light text-charcoal">Design Candidates</h1>
                                <button onClick={resetWizard} className="text-xs text-stone-500 hover:text-charcoal underline">New Project</button>
                            </div>

                            {/* Candidate Gallery */}
                            <div className="grid grid-cols-3 gap-3 mb-6">
                                {candidates.map((c) => (
                                    <div
                                        key={c.id}
                                        onClick={() => handleSelectCandidate(c)}
                                        className={`relative rounded-xl overflow-hidden border-2 cursor-pointer transition-all bg-stone-50 ${selectedCandidateId === c.id ? 'border-charcoal ring-2 ring-charcoal/20' : 'border-stone-100 hover:border-stone-300'}`}
                                    >
                                        <div className="h-20 flex items-center justify-center">
                                            <span className="text-[10px] font-bold text-stone-400 uppercase">Option {c.id + 1}</span>
                                        </div>
                                        <div className={`absolute top-0 right-0 px-2 py-0.5 rounded-bl-lg text-[10px] font-bold ${selectedCandidateId === c.id ? 'bg-charcoal text-white' : 'bg-white/80 text-stone-600'}`}>
                                            Score: {Math.round(c.score)}
                                        </div>
                                    </div>
                                ))}
                            </div>

                            {/* Metrics for selected */}
                            <div className="flex-1 overflow-y-auto pr-2">
                                <div className="flex items-center justify-between mb-4 mt-2">
                                    <h3 className="text-sm font-semibold text-stone-800">Analysis</h3>
                                </div>
                                <div className="space-y-4 mb-6">
                                    {[
                                        { label: 'Efficiency', val: stats.efficiency, color: '#10b981' },
                                        { label: 'Privacy', val: stats.privacy, color: '#6366f1' },
                                        { label: 'Daylight', val: stats.daylight, color: '#f59e0b' },
                                        { label: 'Circulation', val: stats.circulation, color: '#ec4899' }
                                    ].map((stat, i) => (
                                        <div key={i}>
                                            <div className="flex justify-between text-xs mb-1">
                                                <span className="text-stone-600 font-medium">{stat.label}</span>
                                                <span className="text-stone-400">{stat.val}%</span>
                                            </div>
                                            <div className="h-1.5 w-full bg-stone-100 rounded-full overflow-hidden">
                                                <div className="h-full rounded-full" style={{ width: `${stat.val}%`, backgroundColor: stat.color }} />
                                            </div>
                                        </div>
                                    ))}
                                </div>

                                {/* Generated Area Distribution Pie */}
                                <div className="mb-6 h-56 bg-white rounded-xl border border-stone-200 p-4 flex flex-col relative w-full items-center justify-center shadow-sm">
                                    <div className="absolute top-2 left-3 text-xs font-bold text-stone-400 tracking-wider z-10">GENERATED AREAS</div>
                                    {/* Center Label Placed First */}
                                    <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none pt-4 z-0">
                                        <span className="text-stone-400 text-[10px] font-bold">BUILT</span>
                                        <span className="text-stone-700 text-sm font-bold">{Math.round(layoutSpec.rooms.reduce((acc, r) => acc + r.area, 0))}</span>
                                    </div>
                                    <div className="w-full h-full pt-4 relative z-10">
                                        <ResponsiveContainer width="100%" height="100%">
                                            <PieChart>
                                                <Pie
                                                    data={layoutSpec.rooms}
                                                    cx="50%"
                                                    cy="50%"
                                                    innerRadius={45}
                                                    outerRadius={70}
                                                    paddingAngle={2}
                                                    dataKey="area"
                                                    nameKey="type"
                                                    stroke="none"
                                                    onMouseEnter={(e) => setHoveredRoomId(e.id)}
                                                    onMouseLeave={() => setHoveredRoomId(null)}
                                                >
                                                    {layoutSpec.rooms.map((entry, index) => (
                                                        <Cell key={`cell-${index}`} fill={entry.color || '#e5e7eb'} />
                                                    ))}
                                                </Pie>
                                                <Tooltip content={<CustomTooltip />} />
                                            </PieChart>
                                        </ResponsiveContainer>
                                    </div>
                                </div>

                                {/* Room Legend */}
                                <div className="pt-4 border-t border-stone-100">
                                    <div className="grid grid-cols-2 gap-2">
                                        {layoutSpec.rooms.map((room, idx) => (
                                            <div
                                                key={idx}
                                                className={`flex items-center justify-between p-2 rounded-lg text-xs cursor-pointer ${hoveredRoomId === room.id ? 'bg-stone-100 ring-1 ring-stone-200' : 'hover:bg-stone-50'}`}
                                                onMouseEnter={() => setHoveredRoomId(room.id)}
                                                onMouseLeave={() => setHoveredRoomId(null)}
                                            >
                                                <div className="flex items-center gap-2">
                                                    <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: room.color || '#e5e7eb' }} />
                                                    <span className="text-stone-600 truncate w-20" title={room.type}>{room.type}</span>
                                                </div>
                                                <span className="font-mono text-stone-400">
                                                    {room.requested_area_sqft ? `${room.requested_area_sqft} → ${Math.round(room.area)}` : Math.round(room.area)}
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                                <div className="mt-6 flex flex-col gap-2">
                                    <button
                                        onClick={handleDownloadBlueprint}
                                        className="flex items-center justify-center p-3 bg-blue-50 border border-blue-200 rounded-xl text-sm text-blue-800 hover:bg-blue-100 transition-colors w-full"
                                    >
                                        <Printer size={16} className="mr-2" /> Download 2D Blueprint (.PDF)
                                    </button>
                                    <button
                                        onClick={handleExportSTL}
                                        className="flex items-center justify-center p-3 bg-amber-50 border border-amber-200 rounded-xl text-sm text-amber-800 hover:bg-amber-100 transition-colors w-full"
                                    >
                                        <Box size={16} className="mr-2" /> Download 3D Print (.STL)
                                    </button>
                                </div>
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>
            </motion.div>

            {/* Right Panel: 3D Visualization */}
            <div className="w-full md:w-2/3 h-[50vh] md:h-full bg-stone-100 relative">
                {step === 3 && (
                    <div className="absolute inset-0 flex items-center justify-center bg-stone-100 z-20">
                        {/* Empty placeholder during generation to look cool */}
                        <div className="w-full h-full relative overflow-hidden bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-stone-200 via-stone-100 to-stone-100">
                            <div className="absolute inset-0 bg-[linear-gradient(rgba(0,0,0,0.03)_1px,transparent_1px),linear-gradient(90deg,rgba(0,0,0,0.03)_1px,transparent_1px)] bg-[size:40px_40px] [mask-image:radial-gradient(ellipse_50%_50%_at_50%_50%,#000_70%,transparent_100%)] flex items-center justify-center">
                                <motion.div initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ repeat: Infinity, duration: 2, repeatType: "reverse" }} className="w-32 h-32 border border-charcoal/20 border-dashed rounded-full" />
                            </div>
                        </div>
                    </div>
                )}

                <Canvas camera={{ position: [20, 30, 20], fov: 45, far: 10000 }} gl={{ preserveDrawingBuffer: true }}>
                    <SceneExporter sceneRef={sceneRef} glRef={glRef} />
                    <fog attach="fog" args={['#f5f5f4', 50, 10000]} />
                    <ambientLight intensity={0.5} />
                    <directionalLight position={[10, 20, 10]} intensity={1.5} />
                    <Suspense fallback={null}>
                        <Center>
                            {modelUrl && <Model url={modelUrl} />}
                            {layoutSpec && layoutData && layoutSpec.rooms.map(room => (
                                <InteractiveRoom
                                    key={room.id}
                                    roomId={room.id}
                                    roomPoly={layoutData[room.id]}
                                    setHoveredRoomId={setHoveredRoomId}
                                    isHovered={hoveredRoomId === room.id}
                                    roomSpec={room}
                                    unit={unit}
                                    computeRoomDimensions={computeRoomDimensions}
                                />
                            ))}
                            {hoveredPoly && <RoomHighlight roomPoly={hoveredPoly} color={hoveredColor} />}
                            <Grid position={[0, -0.01, 0]} rotation={[Math.PI / 2, 0, 0]} args={[50, 50]} sectionSize={gridSize * 5} sectionThickness={1.5} cellThickness={0.5} cellSize={gridSize} sectionColor="#e5e7eb" fadeDistance={30} />
                        </Center>
                    </Suspense>
                    <OrbitControls makeDefault />
                    <GizmoHelper alignment="bottom-right" margin={[80, 80]}>
                        <axesHelper args={[4]} />
                    </GizmoHelper>
                </Canvas>

                {/* Overlays */}
                <div className="absolute top-6 right-6 pointer-events-none flex flex-col items-end gap-3">
                    {/* Grid Scale Slider */}
                    <div className="bg-white/90 backdrop-blur rounded-lg shadow-sm border border-stone-200 flex flex-col pointer-events-auto p-3 w-40">
                        <div className="flex justify-between items-center mb-1">
                            <span className="text-[9px] uppercase tracking-wider font-bold text-stone-500">Grid Size</span>
                            <span className="text-[10px] font-mono font-bold text-charcoal">{gridSize.toFixed(1)}x</span>
                        </div>
                        <p className="text-[9px] text-stone-400 mb-2 font-mono">
                            1 cell = {unit === 'ft' ? `~${(gridSize * 3.28084).toFixed(1)} ft` : `${gridSize.toFixed(1)} m`}
                        </p>
                        <input
                            type="range"
                            min="0.5"
                            max="3.0"
                            step="0.1"
                            value={gridSize}
                            onChange={(e) => setGridSize(parseFloat(e.target.value))}
                            className="w-full h-1 bg-stone-200 rounded-lg appearance-none cursor-pointer accent-charcoal"
                        />
                    </div>
                </div>

                {!modelUrl && step !== 3 && (
                    <div className="absolute inset-0 flex flex-col items-center justify-center text-stone-400 pointer-events-none">
                        <div className="w-16 h-16 border-2 border-stone-300 rounded-full flex items-center justify-center mb-4">
                            <span className="text-2xl font-light">3D</span>
                        </div>
                        <p>Complete the wizard to generate a layout.</p>
                    </div>
                )}
            </div>
        </div>
    );
};

export default Create;