# Module 0 — Kiểm chứng dữ liệu thanh lý Aave V3

## 1. Mục tiêu

Kiểm tra khả năng thu thập dữ liệu on-chain và tính toán
Liquidation Latency của repository gốc.

## 2. Nguồn dữ liệu

- Blockchain: Ethereum Mainnet
- Protocol: Aave V3 Core
- Data: Blockscout API và Ethereum RPC
- Transaction:
  0x996843238459cc6619832ed5a4f950b9f5843c655134e4611e525781ebd48a76

## 3. Kết quả thực nghiệm

- Liquidation block: 26402092
- Last safe block identified: 26398678
- First liquidatable block identified: 26398679
- Calculated latency: 3413 blocks
- Original latency function: 3413 blocks
- Independent binary search: 3413 blocks
- Results match: True

## 4. Kiểm tra lịch sử

- Lookback window: 7200 blocks
- Chunks checked: 15/15
- Aave liquidation events returned: 50
- Earlier liquidations of borrower found: 0
- HF samples checked: 14
- HF >= 1 among sampled blocks: 0
- RPC errors in sampling: 0

## 5. Hạn chế

- Binary Search dựa trên giả định về ranh giới trạng thái.
- 14 mẫu HF không chứng minh trạng thái liên tục
  trong toàn bộ khoảng 3413 block.
- Chưa xác minh toàn bộ giao dịch lịch sử của repository.
- Chưa xác minh lợi nhuận thực tế của Liquidator.
- Độ trễ lớn chưa chứng minh nguyên nhân là PGA
  hoặc cạnh tranh gas.

## 6. Kết luận

Quy trình truy xuất dữ liệu thật và tính độ trễ đã được
kiểm chứng thành công trên một giao dịch Aave V3.

Kết quả 3413 block là kết quả theo thuật toán đã kiểm thử,
cần tiếp tục đánh giá các giả định trước khi sử dụng
làm bằng chứng về rủi ro hệ thống.
