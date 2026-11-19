// Serverless-функция (Vercel / Node) для безопасной отправки заявок в Telegram
export default async function handler(req, res) {
    if (req.method !== 'POST') {
        return res.status(405).json({ error: 'Method not allowed' });
    }
    const { name, phone, service } = req.body || {};
    if (!name || !phone) {
        return res.status(400).json({ error: 'Имя и телефон обязательны' });
    }

    const token = process.env.TG_BOT_TOKEN;
    const chatId = process.env.TG_CHAT_ID;
    const text = `Новая заявка с сайта:\nИмя: ${name}\nТелефон: ${phone}\nУслуга: ${service || 'Не указана'}`;

    try {
        const tgRes = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chat_id: chatId, text })
        });
        if (!tgRes.ok) throw new Error('Telegram API error');
        return res.status(200).json({ ok: true });
    } catch (e) {
        return res.status(500).json({ error: 'Failed to send' });
    }
}