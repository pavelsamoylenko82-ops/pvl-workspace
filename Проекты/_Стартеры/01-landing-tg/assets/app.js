document.getElementById('leadForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const status = document.getElementById('formStatus');
    const formData = new FormData(e.target);
    const payload = Object.fromEntries(formData.entries());

    status.textContent = 'Отправка...';
    try {
        const res = await fetch('/api/lead', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!res.ok) throw new Error('Ошибка сервера');
        status.textContent = 'Заявка принята. Скоро свяжемся с вами.';
        e.target.reset();
    } catch (err) {
        status.textContent = 'Не удалось отправить. Позвоните нам по номеру на сайте.';
    }
});