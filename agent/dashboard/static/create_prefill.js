/**
 * Shared campaign-create prefill helpers (dashboard + builder).
 * Mirrors agent/utils/create_prefill.py URL + apply contract.
 */
(function (global) {
  function normalizeCampaignType(value, fallback) {
    const v = String(value || '').trim();
    if (v === 'talent_search' || v === 'company_outreach') return v;
    return fallback || 'company_outreach';
  }

  function buildCampaignCreateUrl(opts) {
    opts = opts || {};
    const clientId = String(opts.clientId || '').trim();
    if (!clientId) throw new Error('clientId is required');
    const params = new URLSearchParams();
    params.set('client', clientId);
    params.set('new', '1');
    params.set('type', normalizeCampaignType(opts.campaignType, 'company_outreach'));
    const icpId = String(opts.icpId || '').trim();
    if (icpId) params.set('icp', icpId);
    return (opts.path || '/builder') + '?' + params.toString();
  }

  function mergeClientOptions(diskClients, campaignRows) {
    const map = {};
    (diskClients || []).forEach(function (c) {
      const id = (c && c.client_id) || '';
      if (!id) return;
      map[id] = c.client_name || id;
    });
    (campaignRows || []).forEach(function (c) {
      const id = (c && c.client_id) || '';
      if (!id) return;
      if (!map[id]) map[id] = c.client_name || id;
    });
    return Object.keys(map)
      .sort(function (a, b) {
        return String(map[a]).toLowerCase().localeCompare(String(map[b]).toLowerCase());
      })
      .map(function (id) {
        return { client_id: id, client_name: map[id] };
      });
  }

  /**
   * Apply /builder/api/create-prefill payload onto the create form DOM.
   * deps: optional hooks { syncClientMode, syncIcpMode, syncCreateTypeLabels,
   *   fillCreateIcpSource, applyCreateIcpSourceType }
   */
  async function applyPrefillPayload(prefill, deps) {
    deps = deps || {};
    if (!prefill) return prefill;

    const mode = document.getElementById('create-client-mode');
    if (mode) mode.value = prefill.client_mode === 'new' ? 'new' : 'existing';

    if (prefill.client_mode === 'existing' && prefill.client_id) {
      const sel = document.getElementById('create-client');
      if (sel) {
        const has = [...sel.options].some(function (o) { return o.value === prefill.client_id; });
        if (!has) {
          if (sel.options.length === 1 && !sel.options[0].value) sel.innerHTML = '';
          const opt = document.createElement('option');
          opt.value = prefill.client_id;
          opt.textContent = (prefill.client_name || prefill.client_id)
            + ' (' + prefill.client_id + ')';
          sel.appendChild(opt);
        }
        sel.value = prefill.client_id;
      }
    } else if (prefill.client_id) {
      const idInput = document.getElementById('create-client-new');
      if (idInput) idInput.value = prefill.client_id;
      const nameInput = document.getElementById('create-client-name');
      if (nameInput && !nameInput.value) nameInput.value = prefill.client_name || prefill.client_id;
    }

    if (typeof deps.syncClientMode === 'function') deps.syncClientMode();

    const ctype = normalizeCampaignType(prefill.campaign_type, 'company_outreach');
    document.querySelectorAll('#create-type .segbtn').forEach(function (b) {
      b.classList.toggle('on', b.dataset.type === ctype);
    });
    if (typeof deps.syncCreateTypeLabels === 'function') deps.syncCreateTypeLabels();

    const icpMode = document.getElementById('create-icp-mode');
    if (icpMode) icpMode.value = prefill.icp_mode === 'existing' ? 'existing' : 'new';
    if (typeof deps.syncIcpMode === 'function') deps.syncIcpMode();

    if (typeof deps.fillCreateIcpSource === 'function') {
      await deps.fillCreateIcpSource();
    }

    if (prefill.icp_mode === 'existing' && prefill.icp_id) {
      const icpSel = document.getElementById('create-icp-source');
      if (icpSel) {
        const has = [...icpSel.options].some(function (o) { return o.value === prefill.icp_id; });
        if (has) {
          icpSel.value = prefill.icp_id;
          if (typeof deps.applyCreateIcpSourceType === 'function') {
            deps.applyCreateIcpSourceType();
          }
        }
      }
    }

    if (prefill.suggested_label) {
      const labelEl = document.getElementById('create-label');
      if (labelEl && !String(labelEl.value || '').trim()) {
        labelEl.value = prefill.suggested_label;
      }
    }

    const status = document.getElementById('create-status');
    if (status) {
      if (prefill.errors && prefill.errors.length) {
        status.textContent = prefill.errors.join(' ');
      } else if (prefill.icp_id) {
        status.textContent = 'Prefilled from '
          + (prefill.icp_display_name || prefill.icp_id);
      } else if (prefill.warnings && prefill.warnings.length) {
        status.textContent = prefill.warnings[0];
      }
    }
    return prefill;
  }

  global.PortalCreatePrefill = {
    normalizeCampaignType: normalizeCampaignType,
    buildCampaignCreateUrl: buildCampaignCreateUrl,
    mergeClientOptions: mergeClientOptions,
    applyPrefillPayload: applyPrefillPayload,
  };
})(typeof window !== 'undefined' ? window : globalThis);
