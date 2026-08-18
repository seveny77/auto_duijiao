import os

def rename_bmp_images(folder_path):
    # 1. 获取文件夹中所有bmp文件
    file_list = []
    for filename in os.listdir(folder_path):
        # 只筛选bmp格式图片
        if filename.lower().endswith(".bmp"):
            file_list.append(filename)

    # 2. 按文件名称排序
    file_list.sort()

    # 校验文件数量
    total = len(file_list)
    print(f"找到 {total} 张bmp图片")
    if total == 0:
        print("文件夹内没有bmp文件！")
        return

    # 3. 循环重命名 001 ~ xxx
    for index, old_name in enumerate(file_list, start=1):
        # 三位数字格式化 001,002...100
        new_name = f"{index:03d}.bmp"
        old_path = os.path.join(folder_path, old_name)
        new_path = os.path.join(folder_path, new_name)

        # 避免文件同名冲突
        if old_path == new_path:
            continue

        os.rename(old_path, new_path)
        print(f"{old_name}  -->  {new_name}")

    print("✅ 全部重命名完成！")


if __name__ == "__main__":
    # ========= 修改这里为你的图片文件夹路径 =========
    # Windows示例：r"D:\pictures\img100"
    # Linux/Mac示例："/home/user/images"
    image_folder = r"G:\\0724\\新建文件夹(2)\\3-3mm"

    rename_bmp_images(image_folder)