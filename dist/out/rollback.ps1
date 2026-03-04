# Rollback: stop the bot, then restore your previous release folder from backup.
# Example: copy backup\sovereign_v4.10_rollout\* . 
# Then: python -m sovereign self-test ; python bot.py
Write-Host "1. Stop the bot (Discord /stop or kill process)"
Write-Host "2. Restore previous release folder from backup"
Write-Host "3. Run: python -m sovereign self-test"
Write-Host "4. Run: python bot.py or .\start.ps1"
