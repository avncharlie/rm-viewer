const FOLDER_ICON = `<svg xmlns="http://www.w3.org/2000/svg" width="48" viewBox="0 0 48 48" fill="currentColor">
  <path d="M21.9891 7L24.9891 14H45.5V41H3.5V7H21.9891ZM21.7252 14L20.0109 10H8C7.17157 10 6.5 10.6716 6.5 11.5V24C6.5 18.4772 10.9772 14 16.5 14H21.7252Z"></path>
</svg>`;

const FOLDER_EMPTY_ICON = `<svg xmlns="http://www.w3.org/2000/svg" width="48" viewBox="0 0 48 48" fill="currentColor">
  <path d="M3 7H21.4891L24.4891 14H45V41H3V7ZM16.5 17C10.701 17 6 21.701 6 27.5V38H42V17H16.5ZM19.5109 10H7.5C6.67157 10 6 10.6716 6 11.5V14H21.2252L19.5109 10Z"></path>
</svg>`;

function renderFolders(folders) {
  const grid = document.getElementById('folder_grid');
  grid.innerHTML = '';
  
  folders.forEach(folder => {
    const btn = document.createElement('button');
    btn.className = 'folder';
    btn.innerHTML = `
      ${folder.empty ? FOLDER_EMPTY_ICON : FOLDER_ICON}
      <span>${folder.name}</span>
    `;
    grid.appendChild(btn);
  });
}

function renderDocuments(documents) {
  const grid = document.getElementById('document_grid');
  grid.innerHTML = '';
  
  documents.forEach(doc => {
    const div = document.createElement('div');
    div.className = 'document';
    div.innerHTML = `
      <div class='thumbnail ${doc.type}_thumbnail'>
        <img src="${doc.thumbnail}" width="100%"> 
      </div>
      <div class='doc_text1'>${doc.text1}</div>
      <div class='doc_text2'>${doc.text2}</div>
    `;
    grid.appendChild(div);
  });
}

// Usage:
renderFolders([
  { name: 'Articles', empty: false },
  { name: 'Books', empty: false },
  { name: 'Comics', empty: false },
  { name: 'Development', empty: false },
  { name: 'Papers', empty: false },
  { name: 'Recipes', empty: true },
]);

// Usage:
renderDocuments([
  { type: 'notebook', thumbnail: '/rmviewer.png', text1: 'RMViewer', text2: 'Page 1 of 2' },
  { type: 'pdf', thumbnail: '/getting_started.png', text1: 'Getting started', text2: 'Page 5 of 9' },
  { type: 'ebook', thumbnail: '/everybody_always.png', text1: 'Everybody, always', text2: '8% read' },
]);

/////////////////////////////////////////////////////////////

const BREADCRUMB_ARROW = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" fill="currentColor">
  <path d="M15.8787 8.99998L18 6.87866L35.1213 24L18 41.1213L15.8787 39L27.6967 27.182C29.4541 25.4246 29.4541 22.5754 27.6967 20.818L15.8787 8.99998Z"></path>
</svg>`;

function renderBreadcrumbs(path) {
  const container = document.getElementById('breadcrumbs');
  container.innerHTML = '';

  path.forEach((item, index) => {
    const isFirst = index === 0;
    const isLast = index === path.length - 1;

    // Add arrow before all items except first
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
  // Implement your navigation logic here
}

// Usage:
renderBreadcrumbs([
  { id: 'root', name: 'My files' },
  { id: 'books', name: 'Books' },
  { id: 'theology', name: 'Systematic Theology' },
  { id: 'theology', name: 'Systematic Theology' },
]);

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
    
    if (wasSelected) {
      // Toggle ascending/descending
      option.classList.toggle('desc');
    } else {
      // Select new option
      sortOptions.forEach(o => {
        o.classList.remove('selected');
        o.classList.remove('desc');
      });
      option.classList.add('selected');
    }
    
    sortLabel.textContent = option.querySelector('span').textContent;
    
    // Trigger your sort logic here
    const sortType = option.dataset.sort;
    const isDesc = option.classList.contains('desc');
    console.log('Sort by:', sortType, isDesc ? 'desc' : 'asc');
    
    // Dropdown stays open - user can click header or outside to close
  });
});

const gridSizes = {
  large: { desktop: '280px', mobile: '200px' },
  medium: { desktop: '200px', mobile: '170px' },
  small: { desktop: '150px', mobile: '100px' },
  list: { desktop: '100%', mobile: '100%' }
};

// Grid option click
gridOptions.forEach(option => {
  option.addEventListener('click', (e) => {
    e.stopPropagation();
    gridOptions.forEach(o => o.classList.remove('selected'));
    option.classList.add('selected');
    
    const gridType = option.dataset.grid;
    gridLabel.textContent = gridLabels[gridType];
    
    // Apply grid sizes
    const sizes = gridSizes[gridType];
    document.documentElement.style.setProperty('--grid-min-width', sizes.desktop);
    document.documentElement.style.setProperty('--grid-min-width-mobile', sizes.mobile);
    
    console.log('Grid:', gridType);
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
  // Close sort dropdown if open
  sortDropdown.classList.add('hidden');
  sortWidget.classList.remove('open');
});

searchInput.addEventListener('blur', () => {
  // Small delay to allow clicking on search results if needed
  setTimeout(() => {
    toolbar.classList.remove('search_focused');
  }, 150);
});
