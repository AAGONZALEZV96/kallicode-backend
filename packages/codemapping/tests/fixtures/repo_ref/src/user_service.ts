export class UserService {
  private users: Map<string, User> = new Map();

  async getUserWithOrders(userId: string): Promise<UserWithOrders> {
    const user = this.users.get(userId);

    function enrichOrder(order: Order): EnrichedOrder {
      return { ...order, formattedTotal: `$${order.total.toFixed(2)}` };
    }

    return {
      ...user,
      orders: user.orders.map(enrichOrder),
    };
  }
}