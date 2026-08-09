let conversationHistory = [];

async function sendMessage() {
    const input = document.getElementById('user-input');
    const message = input.value.trim();
    if (!message) return;

    input.value = '';
    appendMessage('user', message);

    // Prepare AI message bubble
    const aiMessageDiv = appendMessage('ai', '...');
    const contentDiv = aiMessageDiv.querySelector('.content');
    contentDiv.innerHTML = '<span class="typing"></span>';

    try {
        const response = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: message,
                history: conversationHistory
            })
        });

        if (!response.ok) throw new Error('Failed to connect');

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let fullReply = '';
        contentDiv.innerHTML = '';

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value);
            fullReply += chunk;
            
            // Use marked.js for real-time markdown rendering
            contentDiv.innerHTML = marked.parse(fullReply);
            
            // Auto scroll
            const container = document.getElementById('chat-container');
            container.scrollTop = container.scrollHeight;
        }

        conversationHistory.push({ role: 'user', content: message });
        conversationHistory.push({ role: 'assistant', content: fullReply });
        
        // Add syntax highlighting
        contentDiv.querySelectorAll('pre code').forEach((block) => {
            hljs.highlightElement(block);
        });

    } catch (error) {
        contentDiv.innerHTML = "Error: " + error.message;
    }
}

function appendMessage(role, text) {
    const container = document.getElementById('chat-container');
    const div = document.createElement('div');
    div.className = `message ${role}-message`;
    
    div.innerHTML = `
        <div class="avatar">${role === 'user' ? 'U' : 'AI'}</div>
        <div class="content">${role === 'user' ? text : marked.parse(text)}</div>
    `;
    
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
}

function clearChat() {
    document.getElementById('chat-container').innerHTML = '';
    conversationHistory = [];
}

// Handle Enter Key
document.getElementById('user-input').addEventListener('keypress', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});