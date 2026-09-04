document.getElementById('convertForm').addEventListener('submit', async e => {
  e.preventDefault();

  const s = document.getElementById('status');
  s.textContent = 'Processing…';

  const fd = new FormData(e.target);
  const tool = fd.get('tool');

  try {
    if (tool === 'pdf-word') {
      const fileInput = document.getElementById('files');

      if (!fileInput.files.length) {
        throw new Error('Please choose a PDF file.');
      }

      const startForm = new FormData();
      startForm.append('file', fileInput.files[0]);

      const startResponse = await fetch('/api/pdf-word/start', {
        method: 'POST',
        body: startForm
      });

      if (!startResponse.ok) {
        let message = 'Conversion failed';

        try {
          const data = await startResponse.json();
          message = data.detail || message;
        } catch (_) {}

        throw new Error(message);
      }

      const startData = await startResponse.json();
      const jobId = startData.job_id;

      if (!jobId) {
        throw new Error('Missing conversion job ID.');
      }

      s.textContent = 'Converting PDF to editable Word…';

      while (true) {
        await new Promise(resolve => setTimeout(resolve, 2000));

        const statusResponse = await fetch(
          `/api/pdf-word/status/${jobId}`
        );

        if (!statusResponse.ok) {
          throw new Error('Could not check conversion status.');
        }

        const statusData = await statusResponse.json();

        if (statusData.status === 'error') {
          throw new Error(
            statusData.error || 'Conversion failed.'
          );
        }

        if (statusData.status === 'done') {
          break;
        }

        s.textContent = 'Converting PDF to editable Word…';
      }

      s.textContent = 'Preparing download…';

      const downloadResponse = await fetch(
        `/api/pdf-word/download/${jobId}`
      );

      if (!downloadResponse.ok) {
        let message = 'Download failed';

        try {
          const data = await downloadResponse.json();
          message = data.detail || message;
        } catch (_) {}

        throw new Error(message);
      }

      const blob = await downloadResponse.blob();

      const url = URL.createObjectURL(blob);

      const a = document.createElement('a');
      a.href = url;
      a.download = 'converted.docx';

      document.body.appendChild(a);
      a.click();
      a.remove();

      setTimeout(() => {
        URL.revokeObjectURL(url);
      }, 5000);

      s.textContent = 'Done. Word document downloaded.';
      return;
    }

    const r = await fetch('/api/convert', {
      method: 'POST',
      body: fd
    });

    if (!r.ok) {
      let message = 'Conversion failed';

      try {
        const j = await r.json();
        message = j.detail || message;
      } catch (_) {}

      throw new Error(message);
    }

    const blob = await r.blob();

    let name = 'result';

    const cd =
      r.headers.get('content-disposition') || '';

    const m =
      cd.match(/filename="?([^";]+)"?/);

    if (m) {
      name = m[1];
    }

    const url =
      URL.createObjectURL(blob);

    const a =
      document.createElement('a');

    a.href = url;
    a.download = name;

    document.body.appendChild(a);
    a.click();
    a.remove();

    setTimeout(() => {
      URL.revokeObjectURL(url);
    }, 5000);

    s.textContent =
      'Done. Your file has been prepared.';

  } catch (err) {
    s.textContent =
      'Error: ' + err.message;
  }
});
