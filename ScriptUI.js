(function () {
  const form = document.getElementById('agenticAgricultureForm');
  const stage = document.getElementById('agenticAgricultureStage');
  const successBox = document.getElementById('agenticAgricultureSuccess');
  const summary = document.getElementById('agenticAgricultureSummary');
  const resetBtn = document.getElementById('agenticAgricultureReset');
  const submitBtn = document.getElementById('agenticAgricultureSubmit');
  const submitText = document.getElementById('submitText');
  const globalErrorBox = document.getElementById('agenticAgricultureGlobalError');

  const segments = Array.from(
    document.querySelectorAll('.agentic-agriculture-segment')
  );

  const localizacaoInput = document.getElementById('Localizacao');
  const inicioInput = document.getElementById('InicioLavoura');
  const fimInput = document.getElementById('FimLavoura');

  let isSubmitting = false;

  const CULTURA_LABELS = {
    soja: 'Soja',
    milho: 'Milho',
    cafe: 'Café',
    'cana-de-acucar': 'Cana-de-açúcar',
    algodao: 'Algodão',
    trigo: 'Trigo',
    arroz: 'Arroz',
    outra: 'Outra'
  };

  const LOCALIZACAO_LABELS = {
    atual: 'Localização atual',
    demonstrativa: 'Propriedade demonstrativa'
  };

  function showGlobalError(message) {
    if (!globalErrorBox) return;
    globalErrorBox.textContent = message;
    globalErrorBox.style.display = 'block';
  }

  function hideGlobalError() {
    if (!globalErrorBox) return;
    globalErrorBox.textContent = '';
    globalErrorBox.style.display = 'none';
  }

  function clearError(id) {
    const group = document.getElementById('group-' + id);
    if (group) group.classList.remove('has-error');
  }

  function setError(id) {
    const group = document.getElementById('group-' + id);
    if (group) group.classList.add('has-error');
  }

  function formatDate(iso) {
    if (!iso) return '';
    const p = iso.split('-');
    return p[2] + '/' + p[1] + '/' + p[0];
  }

  function generateIdempotencyKey() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      const r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    });
  }

  function getCsrfToken() {
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }

  async function loadFieldDraft() {
    try {
      const response = await fetch(
        '/static/data/field-draft.example.json'
      );

      if (response.ok) {
        const draftData = await response.json();

        if (draftData.Cultura) {
          document.getElementById('Cultura').value = draftData.Cultura;
        }

        if (draftData.InicioLavoura) {
          inicioInput.value = draftData.InicioLavoura;
          fimInput.min = draftData.InicioLavoura;
        }

        if (draftData.FimLavoura) {
          if (
            !draftData.InicioLavoura ||
            draftData.FimLavoura >= draftData.InicioLavoura
          ) {
            fimInput.value = draftData.FimLavoura;
          }
        }

        if (draftData.AreaAproximada) {
          document.getElementById('AreaAproximada').value = draftData.AreaAproximada;
        }

        if (draftData.Localizacao) {
          const targetBtn = segments.find(
            b => b.dataset.value === draftData.Localizacao
          );

          if (targetBtn) {
            targetBtn.click();
          }
        }
      }
    } catch (error) {
      console.warn(
        'Nenhum rascunho encontrado ou erro ao carregar:',
        error
      );
    }
  }

  document.addEventListener(
    'DOMContentLoaded',
    loadFieldDraft
  );

  segments.forEach(function (btn) {
    btn.addEventListener('click', function () {
      hideGlobalError();
      segments.forEach(function (b) {
        b.classList.remove('is-active');
        b.setAttribute('aria-checked', 'false');
      });

      btn.classList.add('is-active');
      btn.setAttribute('aria-checked', 'true');

      const selectedValue = btn.dataset.value;
      localizacaoInput.value = selectedValue;

      // Tratamento para geolocalização negada / indisponível
      if (selectedValue === 'atual' && navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(
          function (position) {
            console.log('Posição obtida:', position.coords.latitude, position.coords.longitude);
          },
          function (error) {
            console.warn('Geolocalização negada:', error.message);
            showGlobalError('Não foi possível acessar a localização atual (permissão negada ou indisponível). Selecionando propriedade demonstrativa.');
            const demoBtn = segments.find(b => b.dataset.value === 'demonstrativa');
            if (demoBtn) demoBtn.click();
          },
          { timeout: 10000 }
        );
      }
    });
  });

  inicioInput.addEventListener('change', function () {
    if (inicioInput.value) {
      fimInput.min = inicioInput.value;

      if (
        fimInput.value &&
        fimInput.value < inicioInput.value
      ) {
        fimInput.value = '';
        clearError('FimLavoura');
      }
    }

    clearError('InicioLavoura');
  });

  ['Cultura', 'FimLavoura', 'AreaAproximada'].forEach(
    function (id) {
      const el = document.getElementById(id);
      if (!el) return;

      el.addEventListener('input', function () {
        clearError(id);
      });

      el.addEventListener('change', function () {
        clearError(id);
      });
    }
  );

  function validate() {
    let valid = true;
    const cultura = document.getElementById('Cultura');
    const area = document.getElementById('AreaAproximada');

    if (!cultura.value) {
      setError('Cultura');
      valid = false;
    } else {
      clearError('Cultura');
    }

    if (!inicioInput.value) {
      setError('InicioLavoura');
      valid = false;
    } else {
      clearError('InicioLavoura');
    }

    if (
      !fimInput.value ||
      fimInput.value < inicioInput.value
    ) {
      setError('FimLavoura');
      valid = false;
    } else {
      clearError('FimLavoura');
    }

    if (
      !area.value ||
      Number(area.value) <= 0
    ) {
      setError('AreaAproximada');
      valid = false;
    } else {
      clearError('AreaAproximada');
    }

    return valid;
  }

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    hideGlobalError();

    if (isSubmitting) return;
    if (!validate()) return;

    isSubmitting = true;
    submitBtn.disabled = true;
    if (submitText) submitText.textContent = 'Enviando...';

    const dados = {
      Cultura: document.getElementById('Cultura').value,
      InicioLavoura: inicioInput.value,
      FimLavoura: fimInput.value,
      AreaAproximada: document.getElementById('AreaAproximada').value,
      Localizacao: localizacaoInput.value
    };

    try {
      const response = await fetch('/api/v1/lavoura/detalhes/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken(),
          'Idempotency-Key': generateIdempotencyKey()
        },
        body: JSON.stringify(dados)
      });

      if (!response.ok) {
        if (response.status === 401) {
          throw new Error('Sessão expirada ou não autorizada (401).');
        } else if (response.status === 409) {
          throw new Error('Conflito de dados (409): Requisição duplicada detectada.');
        } else if (response.status === 422) {
          throw new Error('Dados inválidos para processamento (422).');
        } else if (response.status === 429) {
          throw new Error('Muitas requisições enviadas (429). Tente novamente em instantes.');
        } else {
          throw new Error(`Erro inesperado no servidor (${response.status}).`);
        }
      }

      summary.innerHTML =
        '<div><dt>Cultura</dt><dd>' + CULTURA_LABELS[dados.Cultura] + '</dd></div>' +
        '<div><dt>Período</dt><dd>' + formatDate(dados.InicioLavoura) + ' – ' + formatDate(dados.FimLavoura) + '</dd></div>' +
        '<div><dt>Área</dt><dd>' + dados.AreaAproximada.replace('.', ',') + ' ha</dd></div>' +
        '<div><dt>Localização</dt><dd>' + LOCALIZACAO_LABELS[dados.Localizacao] + '</dd></div>';

      stage.style.display = 'none';
      successBox.classList.add('is-visible');

    } catch (error) {
      console.error('Erro no envio:', error);
      showGlobalError(error.message || 'Falha de comunicação com o servidor.');
    } finally {
      isSubmitting = false;
      submitBtn.disabled = false;
      if (submitText) submitText.textContent = 'Enviar detalhes';
    }
  });

  resetBtn.addEventListener('click', function () {
    form.reset();
    hideGlobalError();

    segments.forEach(function (b) {
      b.classList.remove('is-active');
      b.setAttribute('aria-checked', 'false');
    });

    segments[0].classList.add('is-active');
    segments[0].setAttribute('aria-checked', 'true');
    localizacaoInput.value = 'atual';
    fimInput.removeAttribute('min');

    successBox.classList.remove('is-visible');
    stage.style.display = 'block';
  });

})();