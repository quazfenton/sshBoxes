/**
 * sshBox Web Terminal Client
 * WebSocket-based terminal with SSH bridge
 */

// Terminal instance
let term = null;
let fitAddon = null;
let webLinksAddon = null;

// WebSocket connection
let ws = null;
let isConnected = false;

// Session state
let sessionState = {
    sessionId: null,
    token: null,
    profile: null,
    host: null,
    port: null,
    user: null,
    ttl: null,
    timeRemaining: null,
    isObserver: false,
    isInterview: false
};

// Timer interval
let timerInterval = null;

// Initialize terminal on load
document.addEventListener('DOMContentLoaded', () => {
    initTerminal();
    initEventListeners();
    checkUrlParams();
});

/**
 * Initialize Xterm.js terminal
 */
function initTerminal() {
    const terminalElement = document.getElementById('terminal');
    
    // Create terminal
    term = new Terminal({
        cursorBlink: true,
        cursorStyle: 'block',
        fontSize: 14,
        fontFamily: '"Fira Code", "Cascadia Code", Consolas, monospace',
        theme: {
            background: '#1a1a2e',
            foreground: '#eee',
            cursor: '#e94560',
            cursorAccent: '#1a1a2e',
            selection: 'rgba(233, 69, 96, 0.3)',
            black: '#000000',
            red: '#e94560',
            green: '#4ade80',
            yellow: '#fbbf24',
            blue: '#60a5fa',
            magenta: '#c084fc',
            cyan: '#22d3ee',
            white: '#ffffff',
            brightBlack: '#666666',
            brightRed: '#f87171',
            brightGreen: '#86efac',
            brightYellow: '#fde047',
            brightBlue: '#93c5fd',
            brightMagenta: '#e9d5ff',
            brightCyan: '#67e8f9',
            brightWhite: '#ffffff'
        },
        scrollback: 10000,
        tabStopWidth: 4,
        allowProposedApi: true
    });

    // Load addons
    fitAddon = new FitAddon.FitAddon();
    webLinksAddon = new WebLinksAddon.WebLinksAddon();
    
    term.loadAddon(fitAddon);
    term.loadAddon(webLinksAddon);
    
    // Open terminal
    term.open(terminalElement);
    
    // Fit terminal to container
    fitAddon.fit();
    
    // Handle window resize
    window.addEventListener('resize', () => fitAddon.fit());
    
    // Focus terminal
    term.focus();
    
    // Write welcome message
    term.writeln('\x1b[1;35m╔════════════════════════════════════════════════════════╗\x1b[0m');
    term.writeln('\x1b[1;35m║\x1b[0m  \x1b[1;36msshBox Web Terminal\x1b[0m                                    \x1b[1;35m║\x1b[0m');
    term.writeln('\x1b[1;35m║\x1b[0m  \x1b[36mEphemeral SSH Environments\x1b[0m                          \x1b[1;35m║\x1b[0m');
    term.writeln('\x1b[1;35m╚════════════════════════════════════════════════════════╝\x1b[0m');
    term.writeln('');
    term.writeln('Click \x1b[1;33mConnect\x1b[0m to start a new session or enter a token.');
    term.writeln('');
}

/**
 * Initialize event listeners
 */
function initEventListeners() {
    // Terminal input
    term.onData(data => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'terminal_input',
                data: data
            }));
        }
    });

    // Connect button
    document.getElementById('btn-connect').addEventListener('click', () => {
        showConnectModal();
    });

    // Disconnect button
    document.getElementById('btn-disconnect').addEventListener('click', disconnect);

    // Share button
    document.getElementById('btn-share').addEventListener('click', showShareModal);

    // Copy link button
    document.getElementById('btn-copy-link').addEventListener('click', copyObserverLink);

    // Modal buttons
    document.getElementById('btn-modal-connect').addEventListener('click', connectWithToken);
    document.getElementById('btn-modal-cancel').addEventListener('click', hideConnectModal);
    document.getElementById('btn-copy-share').addEventListener('click', copyShareLink);
    document.getElementById('btn-close-share').addEventListener('click', hideShareModal);

    // Chat
    document.getElementById('btn-send').addEventListener('click', sendChatMessage);
    document.getElementById('chat-input').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') sendChatMessage();
    });

    // Token input enter key
    document.getElementById('token-input').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') connectWithToken();
    });
}

/**
 * Check URL parameters for token or session
 */
function checkUrlParams() {
    const params = new URLSearchParams(window.location.search);
    const token = params.get('token');
    const session = params.get('session');
    const observer = params.get('observer');

    if (token) {
        document.getElementById('token-input').value = token;
        connectWithToken();
    } else if (session) {
        // Observer mode
        sessionState.isObserver = observer === 'true';
        connectToSession(session);
    } else {
        showConnectModal();
    }
}

/**
 * Show connect modal
 */
function showConnectModal() {
    document.getElementById('connect-modal').classList.add('show');
    document.getElementById('token-input').focus();
}

/**
 * Hide connect modal
 */
function hideConnectModal() {
    document.getElementById('connect-modal').classList.remove('show');
}

/**
 * Show share modal
 */
function showShareModal() {
    const shareLink = `${window.location.origin}/web/?session=${sessionState.sessionId}&observer=true`;
    document.getElementById('share-link').value = shareLink;
    document.getElementById('share-modal').classList.add('show');
}

/**
 * Hide share modal
 */
function hideShareModal() {
    document.getElementById('share-modal').classList.remove('show');
}

/**
 * Copy observer link to clipboard
 */
function copyObserverLink() {
    const link = `${window.location.origin}/web/?session=${sessionState.sessionId}&observer=true`;
    navigator.clipboard.writeText(link).then(() => {
        alert('Observer link copied to clipboard!');
    });
}

/**
 * Copy share link to clipboard
 */
function copyShareLink() {
    const link = document.getElementById('share-link').value;
    navigator.clipboard.writeText(link).then(() => {
        alert('Link copied to clipboard!');
        hideShareModal();
    });
}

/**
 * Connect with token from modal
 */
async function connectWithToken() {
    const token = document.getElementById('token-input').value.trim();
    
    if (!token) {
        alert('Please enter a token');
        return;
    }

    hideConnectModal();
    
    try {
        // Request session from gateway
        const response = await fetch('/request', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                token: token,
                pubkey: await generateSSHKey(),
                profile: 'dev',
                ttl: 1800
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail?.message || 'Connection failed');
        }

        const connectionInfo = await response.json();
        sessionState = {
            ...sessionState,
            ...connectionInfo,
            token: token,
            ttl: 1800,
            timeRemaining: 1800
        };

        // Connect to WebSocket bridge
        connectToSession(connectionInfo.session_id);

    } catch (error) {
        alert(`Connection error: ${error.message}`);
        showConnectModal();
    }
}

/**
 * Generate SSH key pair (client-side)
 */
async function generateSSHKey() {
    // For simplicity, we'll use a placeholder
    // In production, use Web Crypto API
    return 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI' + 
           Array(50).fill(0).map(() => 
               'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
               [Math.floor(Math.random() * 62)]
           ).join('') + 
           ' web-terminal@sshbox';
}

/**
 * Connect to WebSocket bridge
 */
function connectToSession(sessionId) {
    updateStatus('connecting');
    term.writeln('\r\n\x1b[33mConnecting to session...\x1b[0m');

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/${sessionId}`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        isConnected = true;
        updateStatus('connected');
        term.writeln('\r\n\x1b[32mConnected!\x1b[0m\r\n');
        
        // Update UI
        document.getElementById('session-id').textContent = sessionId;
        document.getElementById('profile-value').textContent = sessionState.profile || 'dev';
        document.getElementById('host-value').textContent = sessionState.host || '-';
        document.getElementById('port-value').textContent = sessionState.port || '-';
        
        // Start timer
        startTimer();
        
        // Enable/disable buttons
        document.getElementById('btn-connect').disabled = true;
        document.getElementById('btn-disconnect').disabled = false;
    };

    ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        
        switch (message.type) {
            case 'terminal_output':
                term.write(message.data);
                break;
            case 'chat_message':
                addChatMessage(message.from, message.text, message.isObserver);
                break;
            case 'session_info':
                updateSessionInfo(message.info);
                break;
            case 'error':
                term.writeln(`\r\n\x1b[31mError: ${message.message}\x1b[0m`);
                break;
        }
    };

    ws.onclose = () => {
        isConnected = false;
        updateStatus('disconnected');
        term.writeln('\r\n\x1b[31mDisconnected from session.\x1b[0m');
        
        // Clear timer
        if (timerInterval) clearInterval(timerInterval);
        
        // Reset buttons
        document.getElementById('btn-connect').disabled = false;
        document.getElementById('btn-disconnect').disabled = true;
    };

    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        term.writeln('\r\n\x1b[31mConnection error.\x1b[0m');
    };
}

/**
 * Disconnect from session
 */
function disconnect() {
    if (ws) {
        ws.close();
    }
    
    // Optionally destroy the session
    if (sessionState.sessionId) {
        fetch('/destroy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionState.sessionId })
        }).catch(console.error);
    }
}

/**
 * Update connection status
 */
function updateStatus(status) {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    
    dot.className = 'status-dot ' + status;
    
    switch (status) {
        case 'connected':
            text.textContent = 'Connected';
            break;
        case 'connecting':
            text.textContent = 'Connecting...';
            break;
        case 'disconnected':
            text.textContent = 'Disconnected';
            break;
    }
}

/**
 * Start session timer
 */
function startTimer() {
    if (timerInterval) clearInterval(timerInterval);
    
    timerInterval = setInterval(() => {
        if (sessionState.timeRemaining > 0) {
            sessionState.timeRemaining--;
            updateTimerDisplay();
        } else {
            clearInterval(timerInterval);
            term.writeln('\r\n\x1b[31mSession expired!\x1b[0m');
        }
    }, 1000);
}

/**
 * Update timer display
 */
function updateTimerDisplay() {
    const minutes = Math.floor(sessionState.timeRemaining / 60);
    const seconds = sessionState.timeRemaining % 60;
    
    const timerElement = document.getElementById('timer');
    timerElement.textContent = `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
    
    // Warning when less than 5 minutes
    if (sessionState.timeRemaining < 300) {
        timerElement.classList.add('warning');
    } else {
        timerElement.classList.remove('warning');
    }
}

/**
 * Update session info
 */
function updateSessionInfo(info) {
    if (info.profile) {
        sessionState.profile = info.profile;
        document.getElementById('profile-value').textContent = info.profile;
    }
    if (info.ttl) {
        sessionState.ttl = info.ttl;
        sessionState.timeRemaining = info.ttl;
    }
    if (info.is_interview) {
        sessionState.isInterview = true;
        document.getElementById('interview-banner').classList.remove('hidden');
        if (info.problem) {
            document.getElementById('interview-problem').textContent = info.problem;
        }
    }
}

/**
 * Send chat message
 */
function sendChatMessage() {
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    
    if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
    
    ws.send(JSON.stringify({
        type: 'chat_message',
        text: text
    }));
    
    // Add to local chat
    addChatMessage('You', text, false);
    input.value = '';
}

/**
 * Add chat message
 */
function addChatMessage(from, text, isObserver) {
    const messagesDiv = document.getElementById('chat-messages');
    const messageDiv = document.createElement('div');
    messageDiv.className = 'chat-message' + (isObserver ? ' observer' : '');
    messageDiv.textContent = `${from}: ${text}`;
    messagesDiv.appendChild(messageDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}
