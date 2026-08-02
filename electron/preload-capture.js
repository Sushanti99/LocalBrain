const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('captureAPI', {
  getImage: () => ipcRenderer.invoke('capture-get-image'),
  submit: (text) => ipcRenderer.invoke('capture-submit', text),
  cancel: () => ipcRenderer.invoke('capture-cancel'),
});
