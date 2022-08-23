#include csv.ahk

; Reads in a .csv file containing image and go commands
; displays results and logs dumps to file
; alt a to activate
; shift to stop
!a::

CSV_Load("ahk.csv", "data")

FormatTime, StartDateTime, %A_NowUTC%, yyyy-MM-dd_HH.mm.ss
basedir = C:\buffer\ahk\out
dirout = %basedir%\%StartDateTime%
; MsgBox %dirout%
FileCreateDir, %dirout%

Loop, % CSV_TotalRows("data") {



    CSV_ReadCell("Data", loopi, 0) 








	;initiate read
	send {SPACE}
	Sleep, 2000

	ok := 1
	status = ok
	; chip inserted backwards, not in socket, etc
	if WinExist("ahk_class #32770") {
		ok := 0
		status = fail
		WinGetText wintxt, ahk_class #32770
	}
	
	; clear abnormal condition (if any)
	; ex: overcurrent during read
	Send {Esc}
	Sleep, 200



	if (show_data and ok) {
		; activate data view
		send ^e
		; select hex view
		; bytes instead of words
		send {Tab}
		send B
		Sleep, 200
		; hex address instead of dec
		send {Tab}
		send {Tab}
		send X
		send {Up}
		; let user oogle the data
		Sleep, 2000
		; Close hex view
		Send {Esc}


		Sleep, 200
	}

	if (save_data) {
		loops := Format("{:04}", loopi)
		FormatTime, curdt, %A_NowUTC%, yyyy-MM-dd_HH.mm.ss
		fn_prefix = %dirout%\%loops%_%curdt%

		fn = %fn_prefix%.log
		FileAppend, %status%`r`n, %fn%
		FileAppend, <WINTXT>%wintxt%</WINTXT>`r`n, %fn%

		if (ok) {
			; file => save pattern as
			; activate file menu
			; send !f
			Send {Alt}
			Send {Down}
			Send {Down}
			Send {Down}
			Send {Down}
			send {Enter}
			; save dialogue
			Sleep, 400
			fn = %fn_prefix%.jed
			Send %fn%
			Sleep, 100
			send {Enter}
			Sleep, 100
			; file format options: accept deafult
			send {Enter}
			
			Sleep, 200
		}
	}

	loopi := loopi + 1
	;MsgBox, %loopi%
}




; this makes it so if you press escape, it stops the script, good for infinite loops
; supposedly you keep this at the bottom of the file per stackoverflow
; interfering with closing message boxes
; Esc::ExitApp
; harmless key
Shift::ExitApp
