import EmbedPDF from './embedpdf/embedpdf.js';

// ---------------------------------------------------------------------------
//   EMBEDPDF VIEWER + CUSTOMISATIONS
// ---------------------------------------------------------------------------

let docManager;
let scrollPlugin;
let searchPlugin;
let uiPlugin;
let zoomPlugin;
let viewportPlugin;
let currentPdfUrl;
let currentPdfLastModified = null;
let resolveViewerReady;
const viewerReady = new Promise(r => { resolveViewerReady = r; });

// Set a custom theme to match reMarkable theme
const viewer = EmbedPDF.init({
  type: 'container',
  target: document.getElementById('pdf-viewer'),
  theme: {
    preference: 'light',
    light: {
      accent: {
        primary: 'rgb(55, 50, 47)',
        primaryHover: 'rgb(80, 75, 70)',
        primaryActive: 'rgb(40, 35, 32)',
        primaryLight: 'rgb(236, 230, 218)',
        primaryForeground: 'rgb(249, 246, 241)'
      },
      background: {
        app: 'rgb(249, 246, 241)',
        surface: 'rgb(236, 230, 218)',
        surfaceAlt: 'rgb(224, 218, 204)',
        elevated: 'rgb(249, 246, 241)',
        overlay: 'rgba(55, 50, 47, 0.5)',
        input: 'rgb(249, 246, 241)'
      },
      foreground: {
        primary: 'rgb(55, 50, 47)',
        secondary: 'rgba(55, 50, 47, 0.8)',
        muted: 'rgba(55, 50, 47, 0.5)',
        disabled: 'rgba(55, 50, 47, 0.3)',
        onAccent: 'rgb(249, 246, 241)'
      },
      interactive: {
        hover: 'rgb(236, 230, 218)',
        active: 'rgb(224, 218, 204)',
        selected: 'rgb(249, 246, 241)',
        focus:  'rgb(55, 50, 47)'
      },
      border: {
        default: 'rgb(224, 218, 204)',
        subtle: 'rgb(236, 230, 218)',
        strong: 'rgb(55, 50, 47)'
      }
    }
  },
  disabledCategories: [
    'annotation',
    'redaction',
    'page-settings',
    ...(window.matchMedia('(max-width: 600px)').matches ? ['zoom'] : []), // only set zoom on non-mobile screens
    'mode',
    'ui-menu'
  ]
});
// Hide viewer by default
document.getElementById('pdf-viewer').style.display = 'none';


// Add custom icons to the viewer
//   Wrapped in a IIFE as we await for the reigstry
(async () => {
  const registry = await viewer.registry;
  const commands = registry.getPlugin('commands').provides();
  const ui = registry.getPlugin('ui').provides();
  docManager = registry.getPlugin('document-manager').provides();
  scrollPlugin = registry.getPlugin('scroll').provides();
  searchPlugin = registry.getPlugin('search').provides();
  uiPlugin = ui;
  zoomPlugin = registry.getPlugin('zoom').provides();
  viewportPlugin = registry.getPlugin('viewport')?.provides();

  // Download icon (very left of screen)
  viewer.registerIcon('download', {
    viewBox: '0 0 24 24',
    paths: [
      { d: 'M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2 -2v-2', stroke: 'currentColor', fill: 'none' },
      { d: 'M7 11l5 5l5 -5', stroke: 'currentColor', fill: 'none' },
      { d: 'M12 4l0 12', stroke: 'currentColor', fill: 'none' }
    ]
  });
  commands.registerCommand({
    id: 'custom.download',
    label: 'Download PDF',
    icon: 'download',
    action: () => {
      if (currentPdfUrl) window.open(currentPdfUrl, '_blank');
    }
  });
  // Position on very left of screen (replacing menu button)
  const schema = ui.getSchema();
  const toolbar = schema.toolbars['main-toolbar'];
  const items = JSON.parse(JSON.stringify(toolbar.items));
  const leftGroup = items.find(item => item.id === 'left-group');
  if (leftGroup) {
    const idx = leftGroup.items.findIndex(item => item.id === 'document-menu-button');
    if (idx !== -1) {
      leftGroup.items[idx] = {
        type: 'command-button',
        id: 'download-button',
        commandId: 'custom.download',
        variant: 'icon'
      };
    }
  }

  // Close icon (very right of screen)
  viewer.registerIcon('close-x', {
    viewBox: '0 0 24 24',
    paths: [
      { d: 'M18 6l-12 12', stroke: 'currentColor', fill: 'none' },
      { d: 'M6 6l12 12', stroke: 'currentColor', fill: 'none' }
    ]
  });
  commands.registerCommand({
    id: 'custom.close',
    label: 'Close',
    icon: 'close-x',
    action: () => {
      localStorage.removeItem('rmviewer.pdfItemId');
      localStorage.removeItem('rmviewer.pdfPage');
      currentPdfLastModified = null;
      document.body.style.overflow = '';
      const el = document.getElementById('pdf-viewer');
      el.classList.add('closing');
      el.addEventListener('transitionend', () => {
        el.style.display = 'none';
        el.classList.remove('closing');
      }, { once: true });
    }
  });
  // Position on very left of right (replacing menu button)
  const rightGroup = items.find(item => item.id === 'right-group');
  if (rightGroup) {
    const idx = rightGroup.items.findIndex(item => item.id === 'comment-button');
    if (idx !== -1) {
      rightGroup.items[idx] = {
        type: 'command-button',
        id: 'close-button',
        commandId: 'custom.close',
        variant: 'icon'
      };
    }
  }

  ui.mergeSchema({
    toolbars: { 'main-toolbar': { ...toolbar, items } }
  });

  // Track page changes to persist current page for reload recovery
  scrollPlugin.onPageChange((event) => {
    if (event.documentId === currentDocId) {
      localStorage.setItem('rmviewer.pdfPage', event.pageNumber);
    }
  });

  resolveViewerReady();
})();

// EmbedPDF also by default hides the pan and pointer button on small screens,
// but we don't want this behaviour. So we add a <style> with CSS to force it
// to stay on screen no matter what.
function forcePanPointerOnScreen() {
  const container = document.querySelector('embedpdf-container');
  if (!container?.shadowRoot?.querySelector('[data-epdf-i="pan-button"]')) return false;
  if (container.shadowRoot.querySelector('#epdf-toolbar-fix')) return true;

  const style = document.createElement('style');
  style.id = 'epdf-toolbar-fix';
  style.textContent = `
    [data-epdf-i="pan-button"],
    [data-epdf-i="pointer-button"] {
      display: flex !important;
    }
  `;
  container.shadowRoot.appendChild(style);
  return true;
}
const fixInterval = setInterval(() => { if (forcePanPointerOnScreen()) clearInterval(fixInterval); }, 100);
setTimeout(() => clearInterval(fixInterval), 10000);

// ---------------------------------------------------------------------------
//   API HELPERS
// ---------------------------------------------------------------------------

async function fetchItem(id) {
  const res = await fetch(`/api/tree/${id}`);
  if (!res.ok) return null;
  return res.json();
}

async function fetchChildren(id) {
  const res = await fetch(`/api/tree/${id}/children`);
  if (!res.ok) return [];
  return res.json();
}

async function fetchBatch(ids) {
  if (!ids.length) return {};
  const res = await fetch('/api/tree/batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(ids),
  });
  if (!res.ok) return {};
  return res.json();
}

async function searchDocuments(query) {
  const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
  if (!res.ok) return { query: '', results: [] };
  return res.json();
}

// ---------------------------------------------------------------------------
//   FILE BROWSER
// ---------------------------------------------------------------------------

// currently viewed folders and documents
let foldersData = [];
let documentsData = [];
// current sorting method
let currentSort = { field: 'opened', desc: true };
// current folder ID for navigation
let currentFolderId = 'root';
// search mode state
let inSearchMode = false;
let currentSearchQuery = '';
let savedSort = null; // saved sort state before entering search mode

// for opening documents with embedpdf viewer
let viewerDocCounter = 0;
let currentDocId = null;

// ripped from remarkable's own file viewer website lol
const FOLDER_ICON = `<svg xmlns="http://www.w3.org/2000/svg" width="48" viewBox="0 0 48 48" fill="currentColor">
  <path d="M21.9891 7L24.9891 14H45.5V41H3.5V7H21.9891ZM21.7252 14L20.0109 10H8C7.17157 10 6.5 10.6716 6.5 11.5V24C6.5 18.4772 10.9772 14 16.5 14H21.7252Z"></path>
</svg>`;

const FOLDER_EMPTY_ICON = `<svg xmlns="http://www.w3.org/2000/svg" width="48" viewBox="0 0 48 48" fill="currentColor">
  <path d="M3 7H21.4891L24.4891 14H45V41H3V7ZM16.5 17C10.701 17 6 21.701 6 27.5V38H42V17H16.5ZM19.5109 10H7.5C6.67157 10 6 10.6716 6 11.5V14H21.2252L19.5109 10Z"></path>
</svg>`;

// Sort folders and documents
function sortItems(items, field, desc, isFolder = false) {
  const sorted = [...items].sort((a, b) => {
    let valA, valB;

    switch (field) {
      case 'modified':
        valA = new Date(a.lastModified).getTime();
        valB = new Date(b.lastModified).getTime();
        break;
      case 'opened':
        valA = new Date(a.lastOpened).getTime();
        valB = new Date(b.lastOpened).getTime();
        break;
      case 'created':
        valA = new Date(a.dateCreated).getTime();
        valB = new Date(b.dateCreated).getTime();
        break;
      case 'size':
        valA = isFolder ? (a.totalSize || 0) : (a.pdfSize || a.fileSize || 0);
        valB = isFolder ? (b.totalSize || 0) : (b.pdfSize || b.fileSize || 0);
        break;
      case 'pages':
        valA = isFolder ? a.itemCount : a.pageCount;
        valB = isFolder ? b.itemCount : b.pageCount;
        break;
      case 'results':
        valA = a.hits || 0;
        valB = b.hits || 0;
        break;
      case 'alpha':
        valA = a.name.toLowerCase();
        valB = b.name.toLowerCase();
        return desc ? valB.localeCompare(valA) : valA.localeCompare(valB);
      default:
        return 0;
    }

    return desc ? valB - valA : valA - valB;
  });

  return sorted;
}

// Create html for each folder
function renderFolders(folders) {
  const grid = document.getElementById('folder_grid');
  grid.innerHTML = '';

  const sortedFolders = sortItems(folders, currentSort.field, currentSort.desc, true);

  sortedFolders.forEach(folder => {
    const btn = document.createElement('button');
    btn.className = 'folder';

    const infoText = `${folder.itemCount} item${folder.itemCount !== 1 ? 's' : ''}`;

    btn.innerHTML = `
      ${folder.itemCount === 0 ? FOLDER_EMPTY_ICON : FOLDER_ICON}
      <span>${folder.name}</span>
      <span class="folder_info">${infoText}</span>
    `;
    btn.addEventListener('click', () => navigateTo(folder.id));
    grid.appendChild(btn);
  });
}

// Create html for each document
function renderDocuments(documents) {
  const grid = document.getElementById('document_grid');
  grid.innerHTML = '';

  const sortedDocs = sortItems(documents, currentSort.field, currentSort.desc, false);

  sortedDocs.forEach(doc => {
    const div = document.createElement('button');
    div.className = 'document';

    let secondaryText;
    if (inSearchMode && doc.hits != null) {
      if (doc.hits === 0 && doc.titleMatch) {
        secondaryText = `<span>Title match</span>`;
      } else {
        secondaryText = `<span>${doc.hits} result${doc.hits !== 1 ? 's' : ''}</span>`;
      }
    } else if (doc.type === 'epub') {
      // on hover, books show percent read
      const percent = Math.round((doc.currentPage / doc.pageCount) * 100);
      secondaryText = `
        <span class="doc_text2_default">Page ${doc.currentPage} of ${doc.pageCount}</span>
        <span class="doc_text2_hover">${percent}% read</span>
      `;
    } else {
      secondaryText = `<span>Page ${doc.currentPage} of ${doc.pageCount}</span>`;
    }

    // In search mode, use thumbnail of the first matched page if available
    let thumbnailUrl = doc.thumbnail || '';
    if (inSearchMode && doc.matches && doc.matches.length > 0) {
      const firstMatchPage = doc.matches[0].page - 1; // convert 1-based to 0-based
      thumbnailUrl = `/api/tree/${doc.id}/thumbnail/${firstMatchPage}`;
    }

    const pdfUrl = `/api/tree/${doc.id}/pdf`;

    // In search mode with content matches, open at match page with search;
    // otherwise open normally (title-only matches open at current page)
    const hasContentMatches = doc.matches && doc.matches.length > 0;
    const openPage = (inSearchMode && hasContentMatches)
      ? doc.matches[0].page : doc.currentPage;
    const openSearch = (inSearchMode && hasContentMatches) ? currentSearchQuery : null;

    div.innerHTML = `
      <div class='thumbnail ${doc.type}_thumbnail'>
        <img src="${thumbnailUrl}" width="100%">
      </div>
      <div class='doc_text1'>${doc.name}</div>
      <div class='doc_text2'>${secondaryText}</div>
    `;
    div.style.cursor = 'pointer';
    div.addEventListener('click', () => openPdfViewer(pdfUrl, openPage, openSearch));
    grid.appendChild(div);
  });
}

// Handler for documents - open PDF at page, or with term searched, or just at
// the start of the pdf.
function openPdfViewer(url, pageNumber, searchQuery) {
  if (!docManager) return;
  const prevDocId = currentDocId;
  const docId = 'viewer-doc-' + (++viewerDocCounter);
  currentDocId = docId;
  docManager.openDocumentUrl({ url, documentId: docId, autoActivate: true });

  // Persist PDF state for reload recovery
  const itemIdMatch = url.match(/\/api\/tree\/([^/]+)\/pdf/);
  if (itemIdMatch) {
    localStorage.setItem('rmviewer.pdfItemId', itemIdMatch[1]);
    localStorage.setItem('rmviewer.pdfPage', pageNumber || 1);
  }
  if (prevDocId) docManager.closeDocument(prevDocId);
  currentPdfUrl = url;
  fetch(url, { method: 'HEAD' }).then(r => {
    if (r.ok) currentPdfLastModified = r.headers.get('Last-Modified');
  }).catch(() => {});
  const el = document.getElementById('pdf-viewer');
  el.style.display = '';
  el.classList.remove('closing');
  document.body.style.overflow = 'hidden';
  // opening at searchQuery takes precedence over opening at pageNumber
  const needsSearch = searchQuery && searchPlugin && uiPlugin;
  const needsScroll = !needsSearch && pageNumber && pageNumber > 1 && scrollPlugin;
  if (needsScroll || needsSearch) {
    let unsubscribe;
    unsubscribe = scrollPlugin.onLayoutReady((event) => {
      if (event.documentId === docId) {
        if (needsSearch) {
          uiPlugin.forDocument(docId).toggleSidebar('right', 'main', 'search-panel');
          searchPlugin.forDocument(docId).searchAllPages(searchQuery);
        }
        if (needsScroll) {
          scrollPlugin.forDocument(docId).scrollToPage({ pageNumber, behavior: 'instant' });
        }
        if (unsubscribe) unsubscribe();
      }
    });
  }
}

// Breadcrumbs
const BREADCRUMB_ARROW = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" fill="currentColor">
  <path d="M15.8787 8.99998L18 6.87866L35.1213 24L18 41.1213L15.8787 39L27.6967 27.182C29.4541 25.4246 29.4541 22.5754 27.6967 20.818L15.8787 8.99998Z"></path>
</svg>`;

function renderBreadcrumbs(path) {
  const container = document.getElementById('breadcrumbs');
  container.innerHTML = '';

  path.forEach((item, index) => {
    const isFirst = index === 0;
    const isLast = index === path.length - 1;

    if (!isFirst) {
      container.insertAdjacentHTML('beforeend', BREADCRUMB_ARROW);
    }

    const span = document.createElement('span');
    span.className = 'breadcrumb_item';
    span.textContent = item.name;

    if (isFirst) {
      span.classList.add('breadcrumb_root');
    }
    if (isLast) {
      span.classList.add('breadcrumb_current');
    } else if (!isFirst) {
      span.classList.add('breadcrumb_folder');
    }

    if (!isLast) {
      span.addEventListener('click', () => navigateTo(item.id));
    }

    container.appendChild(span);
  });
}

// ---------------------------------------------------------------------------
//   NAVIGATION
// ---------------------------------------------------------------------------

async function navigateTo(id) {
  if (inSearchMode) exitSearchSort();
  inSearchMode = false;
  currentSearchQuery = '';
  currentFolderId = id;
  localStorage.setItem('rmviewer.folderId', id);

  // Clear search input when navigating
  searchInput.value = '';

  // Fetch folder metadata (for breadcrumbs) and children IDs in parallel
  const [item, childIds] = await Promise.all([
    fetchItem(id),
    fetchChildren(id),
  ]);

  if (!item) return;

  // Batch fetch all children metadata
  const childrenMap = await fetchBatch(childIds);

  // Split into folders and documents
  foldersData = [];
  documentsData = [];
  for (const childId of childIds) {
    const child = childrenMap[childId];
    if (!child) continue;
    if (child.type === 'folder') {
      foldersData.push(child);
    } else {
      documentsData.push(child);
    }
  }

  renderBreadcrumbs(item.path || [{ id: 'root', name: 'My files' }]);
  refreshView();
}

// Sort menu
const sortButton = document.getElementById('sort_button');
const sortDropdown = document.getElementById('sort_dropdown');
const sortWidget = document.getElementById('sort_widget');
const sortLabel = document.getElementById('sort_label');
const sortHeader = document.querySelector('.sort_header');
const gridOptions = document.querySelectorAll('.grid_option');
const gridLabel = document.getElementById('grid_label');

const gridLabels = {
  large: 'Large grid',
  medium: 'Medium grid',
  small: 'Small grid',
  list: 'List view'
};

// Toggle dropdown
sortButton.addEventListener('click', (e) => {
  e.stopPropagation();
  sortWidget.classList.toggle('open');
  sortDropdown.classList.toggle('hidden');
});

// Close dropdown when clicking header
sortHeader.addEventListener('click', () => {
  sortDropdown.classList.add('hidden');
  sortWidget.classList.remove('open');
});

// Sort option click — use event delegation so dynamically added options work
const sortOptionsContainer = document.querySelector('.sort_options');

// Load saved sort preferences from localStorage
{
  const savedField = localStorage.getItem('rmviewer.sort.field');
  const savedDesc = localStorage.getItem('rmviewer.sort.desc');
  if (savedField) currentSort.field = savedField;
  if (savedDesc !== null) currentSort.desc = savedDesc === 'true';
}

// Sync DOM to match currentSort initial value
{
  const allOptions = sortOptionsContainer.querySelectorAll('.sort_option');
  allOptions.forEach(o => { o.classList.remove('selected'); o.classList.remove('desc'); });
  const match = sortOptionsContainer.querySelector(`.sort_option[data-sort="${currentSort.field}"]`);
  if (match) {
    match.classList.add('selected');
    if (currentSort.desc) match.classList.add('desc');
    sortLabel.textContent = match.querySelector('span').textContent;
  }
}

sortOptionsContainer.addEventListener('click', (e) => {
  const option = e.target.closest('.sort_option');
  if (!option) return;

  const wasSelected = option.classList.contains('selected');
  const sortField = option.dataset.sort;

  if (wasSelected) {
    // Toggle ascending/descending
    option.classList.toggle('desc');
    currentSort.desc = option.classList.contains('desc');
  } else {
    // Select new option
    sortOptionsContainer.querySelectorAll('.sort_option').forEach(o => {
      o.classList.remove('selected');
      o.classList.remove('desc');
    });
    option.classList.add('selected');
    currentSort.field = sortField;
    currentSort.desc = false;
  }

  sortLabel.textContent = option.querySelector('span').textContent;

  // Save sort preference (skip transient "results" sort in search mode)
  if (!inSearchMode) {
    localStorage.setItem('rmviewer.sort.field', currentSort.field);
    localStorage.setItem('rmviewer.sort.desc', currentSort.desc);
  }

  // Re-render with new sort
  refreshView();
});

// SVG icons for the results sort option (reuse the same asc/desc icons)
const SORT_ASC_SVG = sortOptionsContainer.querySelector('.sort_asc').outerHTML;
const SORT_DESC_SVG = sortOptionsContainer.querySelector('.sort_desc').outerHTML;

function enterSearchSort() {
  // Save current sort state
  const selectedOption = sortOptionsContainer.querySelector('.sort_option.selected');
  savedSort = {
    field: currentSort.field,
    desc: currentSort.desc,
    selectedDataSort: selectedOption ? selectedOption.dataset.sort : 'modified',
    isDesc: selectedOption ? selectedOption.classList.contains('desc') : false,
  };

  // Add "Results" option at the top
  const resultsBtn = document.createElement('button');
  resultsBtn.className = 'sort_option selected';
  resultsBtn.dataset.sort = 'results';
  resultsBtn.innerHTML = `${SORT_ASC_SVG}${SORT_DESC_SVG}<span>Results</span>`;
  sortOptionsContainer.prepend(resultsBtn);

  // Deselect previous option
  sortOptionsContainer.querySelectorAll('.sort_option').forEach(o => {
    if (o !== resultsBtn) {
      o.classList.remove('selected');
      o.classList.remove('desc');
    }
  });

  // Set sort to results descending (most results first)
  currentSort.field = 'results';
  currentSort.desc = true;
  resultsBtn.classList.add('desc');
  sortLabel.textContent = 'Results';
}

function exitSearchSort() {
  // Remove the "Results" option
  const resultsOption = sortOptionsContainer.querySelector('.sort_option[data-sort="results"]');
  if (resultsOption) resultsOption.remove();

  // Restore previous sort state
  if (savedSort) {
    currentSort.field = savedSort.field;
    currentSort.desc = savedSort.desc;
    const toSelect = sortOptionsContainer.querySelector(`.sort_option[data-sort="${savedSort.selectedDataSort}"]`);
    if (toSelect) {
      sortOptionsContainer.querySelectorAll('.sort_option').forEach(o => {
        o.classList.remove('selected');
        o.classList.remove('desc');
      });
      toSelect.classList.add('selected');
      if (savedSort.isDesc) toSelect.classList.add('desc');
      sortLabel.textContent = toSelect.querySelector('span').textContent;
    }
    savedSort = null;
  }
}

const gridSizes = {
  large: { desktop: '280px', mobile: '200px' },
  medium: { desktop: '200px', mobile: '170px' },
  small: { desktop: '150px', mobile: '100px' },
  list: { desktop: '100%', mobile: '100%' }
};

const folderGrid = document.getElementById('folder_grid');
const documentGrid = document.getElementById('document_grid');

// Grid option click
gridOptions.forEach(option => {
  option.addEventListener('click', (e) => {
    e.stopPropagation();
    gridOptions.forEach(o => o.classList.remove('selected'));
    option.classList.add('selected');

    const gridType = option.dataset.grid;
    gridLabel.textContent = gridLabels[gridType];
    localStorage.setItem('rmviewer.grid', gridType);

    if (gridType === 'list') {
      folderGrid.classList.add('list_view');
      documentGrid.classList.add('list_view');
      document.body.classList.add('list_view_active');
    } else {
      folderGrid.classList.remove('list_view');
      documentGrid.classList.remove('list_view');
      document.body.classList.remove('list_view_active');

      const sizes = gridSizes[gridType];
      document.documentElement.style.setProperty('--grid-min-width', sizes.desktop);
      document.documentElement.style.setProperty('--grid-min-width-mobile', sizes.mobile);
    }
  });
});

// Restore saved grid preference from localStorage
{
  const savedGrid = localStorage.getItem('rmviewer.grid');
  if (savedGrid) {
    const match = document.querySelector(`.grid_option[data-grid="${savedGrid}"]`);
    if (match) match.click();
  }
}

// Close dropdown when clicking outside
document.addEventListener('click', () => {
  sortDropdown.classList.add('hidden');
  sortWidget.classList.remove('open');
});

sortDropdown.addEventListener('click', (e) => {
  // If sort dropdown clicked, stop click propagation so it doesn't trigger
  // global handler and close the dropdown.
  e.stopPropagation();
});

// Download zip button
document.getElementById('download_zip').addEventListener('click', () => {
  window.location.href = '/api/download/zip';
});

// ---------------------------------------------------------------------------
//   SEARCH
// ---------------------------------------------------------------------------

const toolbar = document.getElementById('toolbar');
const searchInput = document.getElementById('search_input');
const searchBar = document.getElementById('search_bar');
const searchClear = document.getElementById('search_clear');

let searchDebounceTimer = null;

function updateSearchClear() {
  searchBar.classList.toggle('has_text', searchInput.value.length > 0);
}

searchClear.addEventListener('click', () => {
  searchInput.value = '';
  updateSearchClear();
  if (inSearchMode) {
    navigateTo(currentFolderId);
  }
});

searchInput.addEventListener('focus', () => {
  toolbar.classList.add('search_focused');
  sortDropdown.classList.add('hidden');
  sortWidget.classList.remove('open');
});

searchInput.addEventListener('blur', () => {
  setTimeout(() => {
    toolbar.classList.remove('search_focused');
  }, 150);
});

searchInput.addEventListener('input', () => {
  clearTimeout(searchDebounceTimer);
  updateSearchClear();
  const query = searchInput.value.trim();

  if (!query) {
    // Empty query — return to current folder
    if (inSearchMode) {
      navigateTo(currentFolderId);
    }
    return;
  }

  searchDebounceTimer = setTimeout(async () => {
    const result = await searchDocuments(query);
    if (!inSearchMode) enterSearchSort();
    inSearchMode = true;
    currentSearchQuery = query;

    // Show search results breadcrumb
    renderBreadcrumbs([
      { id: 'root', name: 'Search results' },
    ]);

    // Hide folders in search mode, show all matching documents
    foldersData = [];
    documentsData = result.results || [];

    refreshView();
  }, 50);
});

function refreshView() {
  renderFolders(foldersData);
  renderDocuments(documentsData);
}

// Initial load — restore saved navigation state or fall back to root
(async () => {
  const savedFolder = localStorage.getItem('rmviewer.folderId') || 'root';

  // Validate folder exists via API
  const folderItem = savedFolder !== 'root' ? await fetchItem(savedFolder) : { id: 'root' };
  if (folderItem) {
    await navigateTo(savedFolder);
  } else {
    localStorage.removeItem('rmviewer.folderId');
    await navigateTo('root');
  }

  // Restore open PDF if any (wait for EmbedPDF viewer to be ready first)
  const savedPdfId = localStorage.getItem('rmviewer.pdfItemId');
  if (savedPdfId) {
    const pdfItem = await fetchItem(savedPdfId);
    if (pdfItem && pdfItem.type !== 'folder') {
      await viewerReady;
      const page = parseInt(localStorage.getItem('rmviewer.pdfPage') || '1', 10);
      openPdfViewer(`/api/tree/${savedPdfId}/pdf`, page, null);
    } else {
      localStorage.removeItem('rmviewer.pdfItemId');
      localStorage.removeItem('rmviewer.pdfPage');
    }
  }
})();

// ---------------------------------------------------------------------------
//   LIVE RELOAD
// ---------------------------------------------------------------------------
//
// Poll /api/generation every 5s. The server increments this counter on each
// /api/rebuild (triggered by syncd after processing new tablet data).
//
// When the generation changes:
//   1. The folder view always refreshes.
//   2. If a PDF is open, we HEAD the PDF URL to compare Last-Modified.
//      - Unchanged file → do nothing (avoids disruptive reloads).
//      - Changed file   → reload the PDF, preserving viewport state.
//      - Missing file   → close the viewer and clean up localStorage.
//
// Viewport preservation works in two chained onLayoutReady steps:
//   1st: the new doc renders at default zoom → we requestZoom(savedZoom).
//   2nd: the zoom-triggered re-layout settles → we scrollTo(savedOffset).
// The two steps are needed because zoom changes the document's total size,
// which would invalidate any scroll position set before it settled.
//
let knownGeneration = null;

setInterval(async () => {
  try {
    const res = await fetch('/api/generation');
    if (!res.ok) return;
    const { generation } = await res.json();
    if (knownGeneration === null) {
      knownGeneration = generation;
      return;
    }
    if (generation === knownGeneration) return;
    knownGeneration = generation;

    await navigateTo(currentFolderId);

    const pdfItemId = localStorage.getItem('rmviewer.pdfItemId');
    if (pdfItemId && currentPdfUrl) {
      const head = await fetch(currentPdfUrl, { method: 'HEAD' });
      if (!head.ok) {
        localStorage.removeItem('rmviewer.pdfItemId');
        localStorage.removeItem('rmviewer.pdfPage');
        currentPdfLastModified = null;
        document.getElementById('pdf-viewer').style.display = 'none';
      } else {
        const newLastMod = head.headers.get('Last-Modified');
        if (newLastMod !== currentPdfLastModified) {
          await viewerReady;
          const savedZoom = zoomPlugin?.getState()?.currentZoomLevel;
          const savedScroll = scrollPlugin?.getMetrics()?.scrollOffset;
          // open page 1, and then set scroll to whereer it was
          openPdfViewer(currentPdfUrl, 1, null);
          if (savedZoom && savedScroll) {
            let unsub;
            unsub = scrollPlugin.onLayoutReady((event) => {
              if (event.documentId === currentDocId) {
                if (unsub) unsub();
                zoomPlugin.forDocument(currentDocId).requestZoom(savedZoom);
                let unsub2;
                unsub2 = scrollPlugin.onLayoutReady((event2) => {
                  if (event2.documentId === currentDocId) {
                    if (unsub2) unsub2();
                    viewportPlugin?.forDocument(currentDocId).scrollTo({ ...savedScroll, behavior: 'instant' });
                  }
                });
              }
            });
          }
        }
      }
    }
  } catch (e) { /* ignore network errors */ }
}, 5000);
