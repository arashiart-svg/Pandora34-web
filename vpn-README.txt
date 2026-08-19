# Подключение к общей папке Pandora34 (WireGuard + диск S:)

Каждому компьютеру — СВОЙ файл .conf из этой папки. Один файл на двух ПК сразу нельзя.

Сервер: 10.10.0.1
Шара: \\10.10.0.1\share
Логин: WORKGROUP\wgshare
Пароль: тот, что выдал владелец (не из этого файла)

Файлы:
  me.conf       — первый ПК владельца     10.10.0.3
  office2.conf  — второй ПК владельца    10.10.0.4
  office3.conf  — третий ПК владельца    10.10.0.5
  partner.conf  — партнёр-сервис 1       10.10.0.2
  partner2.conf — партнёр-сервис 2       10.10.0.6

--- ПК ---

1. Поставить WireGuard: https://www.wireguard.com/install/

2. Add Tunnel → Import tunnel(s) from file → только СВОЙ .conf

3. В туннеле проверить:
   AllowedIPs = 10.10.0.0/24
   Endpoint = 31.130.135.57:51820
   PersistentKeepalive = 25
   Не ставить 0.0.0.0/0

4. Activate. Проверка в cmd:
   ping 10.10.0.1

5. Один раз, PowerShell ОТ АДМИНИСТРАТОРА:
   Set-SmbClientConfiguration -BlockNTLM $false -Force
   Get-NetConnectionProfile
   Set-NetConnectionProfile -InterfaceAlias "ИМЯ_ТУННЕЛЯ" -NetworkCategory Private
   Test-NetConnection 10.10.0.1 -Port 445
   Нужно TcpTestSucceeded : True

6. Диск — только cmd (не PowerShell):
   cmdkey /add:10.10.0.1 /user:WORKGROUP\wgshare /pass
   net use S: \\10.10.0.1\share /persistent:yes

   Или Проводник → \\10.10.0.1\share → другая учётная запись → WORKGROUP\wgshare

Нельзя: свой Microsoft-аккаунт, один .conf на два компа, выкладывать .conf в GitHub.

Диск ограничен 10 ГБ. Конфиги туннелей лежат в папке vpn.
