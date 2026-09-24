// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.

/**
 * DeepSeek PoW WASM Solver — Node.js bridge (with debug)
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

// The website's binary is NOT redistributed in this repository.
// Pin the exact official version we have tested; update consciously if it changes.
const WASM_URL = 'https://fe-static.deepseek.com/chat/static/sha3_wasm_bg.7b9ca65ddd.wasm';
const WASM_SHA256 = 'b3fca8cc072c1defbd60c02266a8e48bd307a1804aaff4314900aea720e72f7d';
const CACHE_PATH = path.join(__dirname, 'cache', 'sha3_wasm_bg.7b9ca65ddd.wasm');
const WASM_PATH = process.env.DS_TTS_POW_WASM_PATH || CACHE_PATH;

function verifyWasm(bytes) {
    const digest = crypto.createHash('sha256').update(bytes).digest('hex');
    if (digest !== WASM_SHA256) {
        throw new Error('PoW WASM checksum mismatch; refusing unverified binary');
    }
    return bytes;
}

async function loadWasmBytes() {
    try {
        return verifyWasm(fs.readFileSync(WASM_PATH));
    } catch (error) {
        if (error.code !== 'ENOENT') throw error;
    }
    if (WASM_PATH !== CACHE_PATH) {
        throw new Error('DS_TTS_POW_WASM_PATH does not exist');
    }
    // Do not follow redirects to third-party hosts; don't log response bodies.
    const response = await fetch(WASM_URL, { redirect: 'error', signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error(`PoW WASM download failed: HTTP ${response.status}`);
    const declaredSize = Number(response.headers.get('content-length') || 0);
    if (declaredSize > 1_000_000) throw new Error('PoW WASM is unexpectedly large');
    const bytes = Buffer.from(await response.arrayBuffer());
    if (bytes.length > 1_000_000) throw new Error('PoW WASM is unexpectedly large');
    verifyWasm(bytes);
    fs.mkdirSync(path.dirname(CACHE_PATH), { recursive: true, mode: 0o700 });
    const tmp = `${CACHE_PATH}.${crypto.randomUUID()}.tmp`;
    try {
        fs.writeFileSync(tmp, bytes, { mode: 0o600, flag: 'wx' });
        fs.renameSync(tmp, CACHE_PATH);
    } finally {
        try { fs.unlinkSync(tmp); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    return bytes;
}

async function main() {
    const input = process.argv[2];
    if (!input) {
        console.error('Usage: node pow_solver.js \'<json_config>\'');
        process.exit(1);
    }
    
    const config = JSON.parse(input);
    
    const wasmBuffer = await loadWasmBytes();
    const wasmModule = await WebAssembly.compile(wasmBuffer);
    const instance = await WebAssembly.instantiate(wasmModule, {});
    const mem = instance.exports.memory;
    
    // Check initial memory
    console.error(`Initial memory pages: ${mem.buffer.byteLength / 65536}`);
    
    const prefix = `${config.salt}_${config.expire_at}_`;
    console.error(`Difficulty: ${config.difficulty}`);
    
    // Write strings to WASM memory
    function writeString(str) {
        const encoded = Buffer.from(str, 'utf-8');
        const length = encoded.length;
        const ptr = instance.exports.__wbindgen_export_0(length, 1);
        console.error(`Allocated ptr=${ptr}, len=${length}, mem pages=${mem.buffer.byteLength / 65536}`);
        const view = new Uint8Array(mem.buffer);
        for (let i = 0; i < length; i++) {
            view[ptr + i] = encoded[i];
        }
        return { ptr, length };
    }
    
    const retptr = instance.exports.__wbindgen_add_to_stack_pointer(-16);
    console.error(`Stack ptr: ${retptr}`);
    
    try {
        const challengeInfo = writeString(config.challenge);
        const prefixInfo = writeString(prefix);
        
        console.error(`Calling wasm_solve(retptr=${retptr}, ch_ptr=${challengeInfo.ptr}, ch_len=${challengeInfo.length}, pfx_ptr=${prefixInfo.ptr}, pfx_len=${prefixInfo.length}, diff=${config.difficulty})`);
        
        instance.exports.wasm_solve(
            retptr,
            challengeInfo.ptr,
            challengeInfo.length,
            prefixInfo.ptr,
            prefixInfo.length,
            config.difficulty
        );
        
        const view = new Int32Array(mem.buffer);
        const status = view[retptr / 4];
        console.error(`Status: ${status}`);
        
        if (status === 0) {
            console.error('wasm_solve returned 0 — no solution found');
            process.exit(1);
        }
        
        const floatView = new Float64Array(mem.buffer);
        const value = floatView[(retptr + 8) / 8];
        const answer = Math.floor(value);
        console.error(`Answer: ${answer}`);
        
        const result = {
            algorithm: config.algorithm,
            challenge: config.challenge,
            salt: config.salt,
            answer: answer,
            signature: config.signature,
            target_path: config.target_path
        };
        
        const response = Buffer.from(JSON.stringify(result)).toString('base64');
        console.log(response);
    } finally {
        instance.exports.__wbindgen_add_to_stack_pointer(16);
    }
}

main().catch(err => {
    console.error('Error:', err.message);
    console.error(err.stack);
    process.exit(1);
});
