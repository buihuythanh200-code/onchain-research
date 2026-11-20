# Module 1 — Mô hình lý thuyết trò chơi cạnh tranh giữa các Liquidator

## 1. Mục tiêu nghiên cứu

Xây dựng mô hình lý thuyết trò chơi bất hợp tác (Non-cooperative Game Theory) nhằm phân tích hành vi cạnh tranh giữa các tác nhân thanh lý (Liquidator) khi một khoản vay trên giao thức Aave V3 có Health Factor (hệ số an toàn) giảm xuống dưới 1.

Mục tiêu là xác định chiến lược cạnh tranh phí ưu tiên của các Liquidator, lợi nhuận tương ứng và trạng thái cân bằng chiến lược.

## 2. Các tác nhân tham gia (Players)

Mô hình ban đầu gồm hai tác nhân:

- **Liquidator A:** Tác nhân thanh lý thứ nhất.
- **Liquidator B:** Tác nhân thanh lý thứ hai.

Mỗi Liquidator hoạt động độc lập và lựa chọn chiến lược nhằm tối đa hóa lợi nhuận của mình.

## 3. Cấu trúc trò chơi (Game Structure)

Mô hình được thiết kế theo các giả định:

- Trò chơi bất hợp tác (Non-cooperative Game).
- Trò chơi tuần tự (Sequential Game).
- Thông tin hoàn hảo (Perfect Information).
- Tập chiến lược hữu hạn (Finite Action Sets).
- Liquidator A đưa ra quyết định trước.
- Liquidator B quan sát quyết định của A và lựa chọn hành động đáp trả.

Đây là mô hình đơn giản hóa hoạt động cạnh tranh giữa các Liquidator, chưa phản ánh đầy đủ cơ chế sắp xếp giao dịch trên Ethereum thực tế.

## 4. Chiến lược của các tác nhân (Strategies)

Mỗi Liquidator có thể lựa chọn:

- **Không tham gia thanh lý:** Từ bỏ cơ hội thanh lý.
- **Tham gia thanh lý:** Lựa chọn một mức phí ưu tiên từ tập các mức phí được quy định trong mô hình.

Trong mô hình cơ sở, giả định rằng Liquidator có mức phí ưu tiên hợp lệ cao hơn sẽ được ưu tiên thực hiện giao dịch. Trường hợp hai tác nhân đặt mức phí bằng nhau cần được xác định bằng một quy tắc xử lý riêng.

## 5. Hàm lợi ích (Payoff Function)

Lợi nhuận của Liquidator thực hiện thanh lý thành công được xác định theo công thức:

**Lợi nhuận = Phần thưởng thanh lý gộp − Chi phí gas − Các chi phí thực hiện khác**

Trong đó:

- **Phần thưởng thanh lý gộp (Gross Liquidation Reward):** Giá trị lợi ích kinh tế mà Liquidator nhận được từ giao dịch thanh lý.
- **Chi phí gas (Gas Cost):** Chi phí cần thiết để thực hiện giao dịch trên Blockchain.
- **Chi phí khác (Other Costs):** Bao gồm chi phí hoán đổi tài sản, trượt giá hoặc chi phí vay nhanh (Flash Loan), nếu có.

Đối với Liquidator không giành được quyền thanh lý, mô hình ban đầu giả định lợi nhuận bằng 0 do giao dịch không được gửi lên Blockchain hoặc không phát sinh chi phí thực thi.

Giả định này sẽ được xem xét và mở rộng trong các giai đoạn nghiên cứu tiếp theo.

## 6. Cân bằng chiến lược (Equilibrium)

Mục tiêu của mô hình là xác định **Cân bằng Nash hoàn hảo trong các trò chơi con (Subgame Perfect Nash Equilibrium — SPNE)**.

Phương pháp dự kiến sử dụng là **Quy nạp ngược (Backward Induction)**.

Theo đó, mô hình sẽ phân tích hành động tối ưu của Liquidator B tại từng tình huống có thể xảy ra, sau đó xác định chiến lược tối ưu của Liquidator A dựa trên các phản ứng đó.

Kết quả SPNE phải thỏa mãn điều kiện các chiến lược tạo thành cân bằng Nash trong mọi trò chơi con.

## 7. Giới hạn của mô hình (Limitations)

Trong giai đoạn đầu, mô hình có các giới hạn:

- Chưa phản ánh đầy đủ cơ chế sắp xếp giao dịch thực tế trên Ethereum.
- Chưa xét các giao dịch riêng tư và MEV Bundles.
- Giả định các tác nhân có thông tin hoàn hảo.
- Chưa xét sự biến động ngẫu nhiên của phí gas.
- Chưa mô hình hóa tác động phản hồi giữa thanh lý tài sản và biến động giá thị trường.
- Chưa kết hợp đầy đủ dữ liệu lịch sử on-chain để ước lượng các tham số.

Các giới hạn này sẽ được xem xét khi mở rộng mô hình và kiểm định bằng dữ liệu thực tế.
8. Ví dụ minh họa và giải cân bằng SPNE bằng phương pháp quy nạp ngược
