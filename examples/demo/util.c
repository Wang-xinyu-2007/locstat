/*
 * 示例：用 C 写一个把字符串反转的小函数。
 * 这一整段是块注释，locstat 会数成注释行。
 */
#include <stdio.h>
#include <string.h>

// 原地反转，不需要额外内存
void reverse(char *s)
{
    size_t n = strlen(s);
    for (size_t i = 0; i < n / 2; i++) {
        char tmp = s[i];
        s[i] = s[n - 1 - i];
        s[n - 1 - i] = tmp;
    }
}

int main(void)
{
    char buf[] = "locstat";
    reverse(buf);
    printf("%s\n", buf); /* 行尾注释：这行仍然是代码 */
    return 0;
}
