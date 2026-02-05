import EmbedPDF from './embedpdf/embedpdf.js';

// ── EmbedPDF viewer ──────────────────────────────────────────────

const viewer = EmbedPDF.init({
  type: 'container',
  target: document.getElementById('pdf-viewer'),
  src: '/RMViewer.pdf',
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
  scroll: {
  },
  disabledCategories: [
    'annotation',
    'redaction',
    'page-settings',
    'zoom',
    'mode',
    'ui-menu'
  ]
});

(async () => {
  const registry = await viewer.registry;
  const commands = registry.getPlugin('commands').provides();
  const ui = registry.getPlugin('ui').provides();

  viewer.registerIcon('download', {
    viewBox: '0 0 24 24',
    paths: [
      { d: 'M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2 -2v-2', stroke: 'currentColor', fill: 'none' },
      { d: 'M7 11l5 5l5 -5', stroke: 'currentColor', fill: 'none' },
      { d: 'M12 4l0 12', stroke: 'currentColor', fill: 'none' }
    ]
  });

  viewer.registerIcon('close-x', {
    viewBox: '0 0 24 24',
    paths: [
      { d: 'M18 6l-12 12', stroke: 'currentColor', fill: 'none' },
      { d: 'M6 6l12 12', stroke: 'currentColor', fill: 'none' }
    ]
  });

  commands.registerCommand({
    id: 'custom.download',
    label: 'Download PDF',
    icon: 'download',
    action: () => {
      const url = viewer.config?.src || '/RMViewer.pdf';
      window.open(url, '_blank');
    }
  });

  commands.registerCommand({
    id: 'custom.close',
    label: 'Close',
    icon: 'close-x',
    action: () => {
      const el = document.getElementById('pdf-viewer');
      el.classList.add('closing');
      el.addEventListener('transitionend', () => {
        el.style.display = 'none';
        el.classList.remove('closing');
      }, { once: true });
    }
  });

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
})();

function fixEmbedPDFToolbar() {
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
const fixInterval = setInterval(() => { if (fixEmbedPDFToolbar()) clearInterval(fixInterval); }, 100);
setTimeout(() => clearInterval(fixInterval), 10000);

// ── File browser ─────────────────────────────────────────────────

const FOLDER_ICON = `<svg xmlns="http://www.w3.org/2000/svg" width="48" viewBox="0 0 48 48" fill="currentColor">
  <path d="M21.9891 7L24.9891 14H45.5V41H3.5V7H21.9891ZM21.7252 14L20.0109 10H8C7.17157 10 6.5 10.6716 6.5 11.5V24C6.5 18.4772 10.9772 14 16.5 14H21.7252Z"></path>
</svg>`;

const FOLDER_EMPTY_ICON = `<svg xmlns="http://www.w3.org/2000/svg" width="48" viewBox="0 0 48 48" fill="currentColor">
  <path d="M3 7H21.4891L24.4891 14H45V41H3V7ZM16.5 17C10.701 17 6 21.701 6 27.5V38H42V17H16.5ZM19.5109 10H7.5C6.67157 10 6 10.6716 6 11.5V14H21.2252L19.5109 10Z"></path>
</svg>`;

// Data stores
let foldersData = [];
let documentsData = [];
let currentSort = { field: 'modified', desc: false };

// Helper functions
function formatDate(date) {
  const now = new Date();
  const d = new Date(date);
  const diffDays = Math.floor((now - d) / (1000 * 60 * 60 * 24));
  
  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  if (diffDays < 7) return `${diffDays} days ago`;
  
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function getDocumentSecondaryText(doc) {
  if (doc.type === 'ebook') {
    const percent = Math.round((doc.currentPage / doc.pageCount) * 100);
    return `Page ${doc.currentPage} of ${doc.pageCount} (${percent}% read)`;
  }
  return `Page ${doc.currentPage} of ${doc.pageCount}`;
}

function getFolderSecondaryText(folder) {
  return `${folder.itemCount} item${folder.itemCount !== 1 ? 's' : ''}`;
}

// Sorting functions
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
        valA = isFolder ? a.totalSize : a.fileSize;
        valB = isFolder ? b.totalSize : b.fileSize;
        break;
      case 'pages':
        valA = isFolder ? a.itemCount : a.pageCount;
        valB = isFolder ? b.itemCount : b.pageCount;
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

function renderFolders(folders) {
  const grid = document.getElementById('folder_grid');
  grid.innerHTML = '';
  
  const sortedFolders = sortItems(folders, currentSort.field, currentSort.desc, true);
  
  sortedFolders.forEach(folder => {
    const btn = document.createElement('button');
    btn.className = 'folder';
    const infoText = getFolderSecondaryText(folder);
    btn.innerHTML = `
      ${folder.itemCount === 0 ? FOLDER_EMPTY_ICON : FOLDER_ICON}
      <span>${folder.name}</span>
      <span class="folder_info">${infoText}</span>
    `;
    grid.appendChild(btn);
  });
}

function renderDocuments(documents) {
  const grid = document.getElementById('document_grid');
  grid.innerHTML = '';
  
  const sortedDocs = sortItems(documents, currentSort.field, currentSort.desc, false);
  
  sortedDocs.forEach(doc => {
    const div = document.createElement('div');
    div.className = 'document';
    
    let secondaryText;
    if (doc.type === 'ebook') {
      const percent = Math.round((doc.currentPage / doc.pageCount) * 100);
      secondaryText = `
        <span class="doc_text2_default">Page ${doc.currentPage} of ${doc.pageCount}</span>
        <span class="doc_text2_hover">${percent}% read</span>
      `;
    } else {
      secondaryText = `<span>Page ${doc.currentPage} of ${doc.pageCount}</span>`;
    }
    
    div.innerHTML = `
      <div class='thumbnail ${doc.type}_thumbnail'>
        <img src="${doc.thumbnail}" width="100%"> 
      </div>
      <div class='doc_text1'>${doc.name}</div>
      <div class='doc_text2'>${secondaryText}</div>
    `;
    grid.appendChild(div);
  });
}

function refreshView() {
  renderFolders(foldersData);
  renderDocuments(documentsData);
}

// Initialize data
foldersData = [
  { 
    name: 'Articles', 
    itemCount: 12, 
    lastModified: '2025-01-30T10:30:00',
    lastOpened: '2025-01-31T08:00:00',
    dateCreated: '2024-06-15T14:00:00',
    totalSize: 45000000
  },
  { 
    name: 'Books', 
    itemCount: 8, 
    lastModified: '2025-01-28T16:45:00',
    lastOpened: '2025-01-29T12:00:00',
    dateCreated: '2024-03-20T09:30:00',
    totalSize: 120000000
  },
  { 
    name: 'Comics', 
    itemCount: 3, 
    lastModified: '2025-01-15T11:00:00',
    lastOpened: '2025-01-20T15:30:00',
    dateCreated: '2024-08-10T18:00:00',
    totalSize: 85000000
  },
  { 
    name: 'Development', 
    itemCount: 25, 
    lastModified: '2025-02-01T09:00:00',
    lastOpened: '2025-02-01T09:00:00',
    dateCreated: '2024-01-05T10:00:00',
    totalSize: 15000000
  },
  { 
    name: 'Papers', 
    itemCount: 7, 
    lastModified: '2025-01-25T14:20:00',
    lastOpened: '2025-01-26T11:00:00',
    dateCreated: '2024-09-01T08:00:00',
    totalSize: 28000000
  },
  { 
    name: 'Recipes', 
    itemCount: 0, 
    lastModified: '2024-12-20T10:00:00',
    lastOpened: '2024-12-25T18:00:00',
    dateCreated: '2024-12-20T10:00:00',
    totalSize: 0
  },
];

documentsData = [
  { 
    type: 'notebook', 
    thumbnail: '/rmviewer.png', 
    name: 'RMViewer',
    currentPage: 1,
    pageCount: 2,
    lastModified: '2025-02-01T08:30:00',
    lastOpened: '2025-02-01T08:30:00',
    dateCreated: '2025-01-15T10:00:00',
    fileSize: 524000
  },
  { 
    type: 'pdf', 
    thumbnail: '/getting_started.png', 
    name: 'Getting started',
    currentPage: 5,
    pageCount: 9,
    lastModified: '2025-01-20T14:00:00',
    lastOpened: '2025-01-28T16:00:00',
    dateCreated: '2024-06-01T12:00:00',
    fileSize: 2150000
  },
  { 
    type: 'ebook', 
    thumbnail: '/everybody_always.png', 
    name: 'Everybody, always',
    currentPage: 24,
    pageCount: 300,
    lastModified: '2025-01-18T20:00:00',
    lastOpened: '2025-01-30T21:00:00',
    dateCreated: '2024-11-10T09:00:00',
    fileSize: 4500000
  },
];

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
    } else if (isLast) {
      span.classList.add('breadcrumb_current');
    } else {
      span.classList.add('breadcrumb_folder');
    }

    if (!isLast) {
      span.addEventListener('click', () => navigateTo(item.id));
    }

    container.appendChild(span);
  });
}

function navigateTo(id) {
  console.log('Navigate to:', id);
}

renderBreadcrumbs([
  { id: 'root', name: 'My files' },
  { id: 'books', name: 'Books' },
  { id: 'theology', name: 'Systematic Theology' },
  { id: 'theology', name: 'Systematic Theology' },
]);

/////////////////////////////////////////////////////////////

const sortButton = document.getElementById('sort_button');
const sortDropdown = document.getElementById('sort_dropdown');
const sortWidget = document.getElementById('sort_widget');
const sortLabel = document.getElementById('sort_label');
const sortHeader = document.querySelector('.sort_header');
const sortOptions = document.querySelectorAll('.sort_option');
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

// Sort option click
sortOptions.forEach(option => {
  option.addEventListener('click', () => {
    const wasSelected = option.classList.contains('selected');
    const sortField = option.dataset.sort;
    
    if (wasSelected) {
      // Toggle ascending/descending
      option.classList.toggle('desc');
      currentSort.desc = option.classList.contains('desc');
    } else {
      // Select new option
      sortOptions.forEach(o => {
        o.classList.remove('selected');
        o.classList.remove('desc');
      });
      option.classList.add('selected');
      currentSort.field = sortField;
      currentSort.desc = false;
    }
    
    sortLabel.textContent = option.querySelector('span').textContent;
    
    // Re-render with new sort
    refreshView();
  });
});

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

// Close dropdown when clicking outside
document.addEventListener('click', () => {
  sortDropdown.classList.add('hidden');
  sortWidget.classList.remove('open');
});

sortDropdown.addEventListener('click', (e) => {
  e.stopPropagation();
});

const toolbar = document.getElementById('toolbar');
const searchInput = document.getElementById('search_input');

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

// Initial render
refreshView();
