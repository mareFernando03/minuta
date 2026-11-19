; Hooks del instalador de Minuta.
; Antes de instalar o desinstalar se cierra todo proceso que corra desde la carpeta del motor: el motor de la app
; (que igual muere con ella) y, sobre todo, el servidor MCP «reuniones» que lanza Claude Code, que si no dejaría
; archivos en uso y el instalador no podría reemplazarlos.
;
; Se usa CIM y no Get-Process: el instalador es de 32 bits, lanza el PowerShell de 32 bits, y desde ahí Get-Process
; no puede leer la ruta de un proceso de 64 bits (la devuelve vacía y no cerraba nada). Probado el 9/10/2026.

!macro CerrarMotor
  nsExec::Exec `powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $$_.ExecutablePath -like '$INSTDIR\motor\*' } | ForEach-Object { Stop-Process -Id $$_.ProcessId -Force }"`
  Pop $0
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro CerrarMotor
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro CerrarMotor
!macroend
