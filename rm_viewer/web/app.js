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
