import telnetlib
import time

# Đọc proxy từ file proxies.txt
with open("proxies.txt", "r") as file:
    proxies = [line.strip() for line in file if line.strip()]  # Lấy từng dòng, bỏ dòng trống

# Test từng proxy
for proxy in proxies:
    try:
        ip, port = proxy.split(":")  # Tách IP và port
        start_time = time.time()
        tn = telnetlib.Telnet(ip, int(port), timeout=5)  # Test connect
        delay = (time.time() - start_time) * 1000  # Delay tính bằng ms
        print(f"{proxy} - Alive - Delay: {delay:.2f}ms")
        tn.close()
    except Exception as e:
        print(f"{proxy} - Dead - Error: {e}")

# Ghi proxy sống vào file mới
alive_proxies = [proxy for proxy in proxies if "Alive" in locals().get(f"status_{proxy}", "")]
with open("alive_proxies.txt", "w") as file:
    file.write("\n".join(alive_proxies))
print(f"Đã lưu {len(alive_proxies)} proxy sống vào alive_proxies.txt")